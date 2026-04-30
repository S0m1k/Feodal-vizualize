import asyncio
import os
import uuid
from datetime import date, datetime, timedelta
from urllib.parse import quote
from fastapi import APIRouter, Request, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from redis import Redis
from database import get_db
from utils.generation import generate_image, build_prompt
from utils.notifications import send_email_sync, create_notification
from utils.common import STONE_TYPES, resolve_public_base_url
import httpx
import logging

router = APIRouter(tags=["client"])

# Алиасы новых типов на старые имена в БД (backward compat)
_MATERIAL_ALIASES: dict[str, str] = {
    "flat_stone": "derbent_stone",
    "textured_stone": "derbent_stone",
}

redis_client = Redis(
    host='localhost', port=6379, decode_responses=True,
    password=os.getenv("REDIS_PASSWORD") or None,
)
logger = logging.getLogger("client_generate")

DAILY_LIMIT = 2  # генераций с одного IP в день


def get_client_ip(request: Request) -> str:
    """Реальный IP клиента с учётом reverse proxy (Caddy/nginx)."""
    for header in ("X-Forwarded-For", "X-Real-IP", "CF-Connecting-IP"):
        val = request.headers.get(header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def can_generate(client_ip: str) -> bool:
    today = date.today().isoformat()
    key = f"limit:ip:{client_ip}:{today}"
    count = redis_client.get(key)
    return True if count is None else int(count) < DAILY_LIMIT


def increment_count(client_ip: str) -> int:
    today = date.today().isoformat()
    key = f"limit:ip:{client_ip}:{today}"
    new_count = redis_client.incr(key)
    if new_count == 1:
        now = datetime.now()
        midnight = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
        redis_client.expire(key, int((midnight - now).total_seconds()))
    return new_count


@router.get("/suppliers")
async def get_suppliers(material_type: str):
    db_type = _MATERIAL_ALIASES.get(material_type, material_type)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT DISTINCT supplier FROM materials WHERE material_type = ?", (db_type,))
        rows = await cursor.fetchall()
    finally:
        await db.close()
    name_map = {"redstone": "Redstone", "redstone_premium": "Redstone Premium", "krasny_kamen": "Красный Камень"}
    return [{"code": row["supplier"], "name": name_map.get(row["supplier"], row["supplier"])} for row in rows]

@router.get("/textures")
async def get_textures(material_type: str, supplier: str):
    db_type = _MATERIAL_ALIASES.get(material_type, material_type)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT name, filename FROM materials WHERE material_type = ? AND supplier = ?",
            (db_type, supplier)
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()
    # URL указывает на реальный путь файла (db_type), а не на alias
    return [{"name": row["name"], "url": f"/textures/{db_type}/{supplier}/{row['filename']}"} for row in rows]

@router.post("/leads")
async def submit_lead(
    name: str = Form(...),
    contact: str = Form(...),
    contact_type: str = Form(...),
):
    if contact_type not in ("email", "phone"):
        raise HTTPException(status_code=422, detail="contact_type must be 'email' or 'phone'")
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO leads (name, contact, contact_type) VALUES (?, ?, ?)",
            (name.strip(), contact.strip(), contact_type),
        )
        await db.commit()
        msg = f"Новый лид: {name} — {contact} ({contact_type})"
        await create_notification(db, "lead", msg)
    finally:
        await db.close()

    import asyncio
    body = (
        f"Новый лид из клиентского визуализатора!\n\n"
        f"Имя:    {name}\n"
        f"{'Email' if contact_type == 'email' else 'Телефон'}:  {contact}\n\n"
        f"— Rstone автомониторинг"
    )
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, send_email_sync, f"👤 Новый лид: {name}", body)

    return {"ok": True}


@router.get("/grout-colors")
async def get_grout_colors():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT name, hex_code FROM grout_colors ORDER BY id")
        rows = await cursor.fetchall()
    finally:
        await db.close()
    return [{"name": row["name"], "hex_code": row["hex_code"]} for row in rows]

async def _run_client_generation(
    request_id: str,
    client_ip: str,
    photo_url: str,
    texture_url: str,
    temp_path: str,
    prompt: str,
    texture: str,
    grout_color_name: str | None,
    category: str,
    material_type: str,
    supplier: str,
) -> None:
    """Фоновая задача генерации для клиентского виджета."""
    try:
        logger.info("client_generate bg start request_id=%s ip=%s", request_id, client_ip)
        result_data = await generate_image([photo_url, texture_url], prompt)
        output_url = result_data.get("output_url")
        if not output_url:
            raise ValueError("Пустой output_url от GenAPI")

        async with httpx.AsyncClient(timeout=60.0) as dl_client:
            img_resp = await dl_client.get(output_url)
            img_resp.raise_for_status()
            img_bytes = img_resp.content

        output_dir = "data/generations/client"
        os.makedirs(output_dir, exist_ok=True)
        output_filename = f"{uuid.uuid4().hex}.jpg"
        output_path = os.path.join(output_dir, output_filename)
        with open(output_path, "wb") as f:
            f.write(img_bytes)

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO generations (user_type, user_id, input_image_path, output_image_path, "
                "prompt, texture_name, grout_color, category, material_type, supplier) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("client", client_ip, temp_path, output_path, prompt,
                 texture, grout_color_name, category, material_type, supplier),
            )
            await db.commit()
        finally:
            await db.close()

        result_url = f"/generated/client/{output_filename}"
        redis_client.setex(f"gen_status:{request_id}", 3600, f"success:{result_url}")
        logger.info(
            "client_generate bg success request_id=%s result_url=%s", request_id, result_url
        )
    except Exception as e:
        logger.error("client_generate bg error request_id=%s: %s", request_id, e)
        redis_client.setex(f"gen_status:{request_id}", 3600, "error")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@router.post("/generate")
async def client_generate(
    request: Request,
    file: UploadFile,
    texture: str = Form(...),
    category: str = Form("facade"),
    material_type: str = Form(...),
    supplier: str = Form(...),
    grout_color_name: str = Form(None),
    use_zone: str = Form(None),
):
    client_ip = get_client_ip(request)
    logger.info(
        "client_generate start ip=%s material_type=%s supplier=%s texture=%s category=%s grout=%s",
        client_ip, material_type, supplier, texture, category, grout_color_name,
    )
    if not can_generate(client_ip):
        return JSONResponse(status_code=429, content={"error": "Лимит исчерпан"})

    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Неверный формат файла")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл превышает 10 МБ")

    temp_dir = "temp/client"
    os.makedirs(temp_dir, exist_ok=True)
    temp_filename = f"{uuid.uuid4().hex}.jpg"
    temp_path = os.path.join(temp_dir, temp_filename)
    with open(temp_path, "wb") as f:
        f.write(contents)

    # Получаем filename текстуры из БД
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT filename FROM materials WHERE name = ? AND material_type = ? AND supplier = ?",
            (texture, material_type, supplier),
        )
        row = await cursor.fetchone()
    finally:
        await db.close()
    if not row:
        os.unlink(temp_path)
        raise HTTPException(status_code=404, detail="Texture not found")

    base_url = resolve_public_base_url(request)
    photo_url = f"{base_url}/temp/client/{temp_filename}"
    texture_url = f"{base_url}/textures/{material_type}/{supplier}/{quote(row['filename'])}"

    grout_hex = None
    if grout_color_name and material_type not in STONE_TYPES:
        db = await get_db()
        try:
            grout_cursor = await db.execute(
                "SELECT hex_code FROM grout_colors WHERE name = ?", (grout_color_name,)
            )
            grout_row = await grout_cursor.fetchone()
        finally:
            await db.close()
        if grout_row:
            grout_hex = grout_row["hex_code"]

    prompt = build_prompt(category, material_type, grout_hex, use_zone=bool(use_zone))

    # Списываем лимит до старта генерации (чтобы нельзя было запустить N параллельных)
    new_count = increment_count(client_ip)
    remaining = max(0, DAILY_LIMIT - new_count)

    request_id = str(uuid.uuid4())
    redis_client.setex(f"gen_status:{request_id}", 3600, "processing")

    asyncio.create_task(_run_client_generation(
        request_id, client_ip, photo_url, texture_url, temp_path,
        prompt, texture, grout_color_name, category, material_type, supplier,
    ))

    logger.info(
        "client_generate accepted ip=%s request_id=%s remaining=%d",
        client_ip, request_id, remaining,
    )
    return {"request_id": request_id, "remaining": remaining}