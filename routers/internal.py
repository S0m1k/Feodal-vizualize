import os
import uuid
import shutil
import httpx
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, UploadFile, Form, File, Query, Request
from database import get_db
from middleware import get_current_manager, get_current_user, get_current_admin
import asyncio
from utils.generation import generate_image, build_prompt, build_plinth_prompt, build_reika_prompt, build_belt_prompt
from utils.notifications import create_notification
from utils.common import STONE_TYPES, resolve_public_base_url
from rauth import get_password_hash
from pydantic import BaseModel
from typing import List
import sqlite3
from redis import Redis
import logging
router = APIRouter()
logger = logging.getLogger("internal_generate")

TEMP_DIR = "temp/internal"
GENERATED_DIR = "data/generations/internal"
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)
redis_client = Redis(
    host='localhost', port=6379, decode_responses=True,
    password=os.getenv("REDIS_PASSWORD") or None,
)


# ===== Модели =====
class UserCreateModel(BaseModel):
    username: str
    password: str
    role: str = "manager"

class GroutColorCreate(BaseModel):
    name: str
    hex_code: str

# ========== Фоновая генерация для сотрудников ==========
async def _run_internal_generation(
    request_id: str,
    user_id: str,
    photo_url: str,
    texture_url: str,
    temp_path: str,
    prompt: str,
    texture: str,
    grout_color_name: str | None,
    category: str,
    material_type: str,
    supplier: str,
    model: str | None = None,
) -> None:
    """Фоновая задача AI-генерации для сотрудников."""
    try:
        logger.info(
            "internal_generate bg start request_id=%s user_id=%s model=%s", request_id, user_id, model
        )
        result_data = await generate_image([photo_url, texture_url], prompt, material_type=material_type, model_override=model)
        output_url = result_data.get("output_url")
        if not output_url:
            raise ValueError("Пустой output_url от GenAPI")

        async with httpx.AsyncClient(timeout=60.0) as dl_client:
            img_resp = await dl_client.get(output_url)
            img_resp.raise_for_status()
            result_bytes = img_resp.content

        user_output_dir = os.path.join(GENERATED_DIR, user_id)
        os.makedirs(user_output_dir, exist_ok=True)
        output_filename = f"{uuid.uuid4().hex}.jpg"
        output_path = os.path.join(user_output_dir, output_filename)
        with open(output_path, "wb") as f:
            f.write(result_bytes)

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO generations (user_type, user_id, input_image_path, output_image_path, "
                "prompt, texture_name, grout_color, category, material_type, supplier, model_used) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("internal", user_id, temp_path, output_path, prompt,
                 texture, grout_color_name, category, material_type, supplier, model),
            )
            await db.commit()
        finally:
            await db.close()

        result_url = f"/generated/internal/{user_id}/{output_filename}"
        redis_client.setex(f"gen_status:{request_id}", 3600, f"success:{result_url}")
        logger.info(
            "internal_generate bg success request_id=%s result_url=%s", request_id, result_url
        )
    except Exception as e:
        logger.error("internal_generate bg error request_id=%s: %s", request_id, e)
        redis_client.setex(f"gen_status:{request_id}", 3600, "error")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@router.post("/generate")
async def internal_generate(
    request: Request,
    file: UploadFile,
    texture: str = Form(...),
    category: str = Form("facade"),
    material_type: str = Form(...),
    supplier: str = Form(...),
    grout_color_name: str = Form(None),
    use_zone: str = Form(None),
    model: str = Form(None),
    current_user = Depends(get_current_manager),
):
    user_id = str(current_user["id"])
    logger.info(
        "internal_generate start user_id=%s material_type=%s supplier=%s texture=%s category=%s grout=%s model=%s",
        user_id, material_type, supplier, texture, category, grout_color_name, model,
    )
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Неверный формат файла — ожидается изображение")
    contents = await file.read()
    if len(contents) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл превышает 15 МБ")

    temp_filename = f"{uuid.uuid4().hex}.jpg"
    temp_path = os.path.join(TEMP_DIR, temp_filename)
    with open(temp_path, "wb") as f:
        f.write(contents)

    base_url = resolve_public_base_url(request)
    host = (request.url.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") and not os.getenv("PUBLIC_BASE_URL"):
        logger.warning(
            "internal_generate: GenAPI скачивает image_urls с вашего сервера. "
            "С localhost это недоступно извне — задайте PUBLIC_BASE_URL."
        )
    photo_url = f"{base_url}/temp/internal/{temp_filename}"

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
        raise HTTPException(status_code=404, detail="Текстура не найдена")

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

    custom_system_prompt = None
    _builtin_slugs = {"standard", "rigel", "riegel_mixed", "cobblestone", "rubble_stone",
                      "flat_stone", "textured_stone", "derbent_stone", "solid", "reika"}
    if material_type and material_type not in _builtin_slugs:
        db2 = await get_db()
        try:
            cur2 = await db2.execute(
                "SELECT system_prompt, default_model FROM custom_material_types WHERE slug = ?",
                (material_type,),
            )
            cmt_row = await cur2.fetchone()
            if cmt_row:
                custom_system_prompt = cmt_row["system_prompt"]
                if not model:
                    model = cmt_row["default_model"]
        finally:
            await db2.close()

    prompt = build_prompt(category, material_type, grout_hex, use_zone=bool(use_zone),
                          custom_system_prompt=custom_system_prompt)

    request_id = str(uuid.uuid4())
    redis_client.setex(f"gen_status:{request_id}", 3600, "processing")

    asyncio.create_task(_run_internal_generation(
        request_id, user_id, photo_url, texture_url, temp_path,
        prompt, texture, grout_color_name, category, material_type, supplier,
        model=model,
    ))

    logger.info(
        "internal_generate accepted user_id=%s request_id=%s", user_id, request_id
    )
    return {"request_id": request_id}
# ========== История ==========
@router.get("/history")
async def get_history(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user_filter: int = Query(None),
    current_user = Depends(get_current_user),
):
    user_role = current_user["role"]
    current_user_id = current_user["id"]
    db = await get_db()
    try:
        if user_role == "admin" and user_filter is not None:
            cursor = await db.execute(
                "SELECT * FROM generations WHERE user_type='internal' AND user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (user_filter, per_page, (page-1)*per_page)
            )
            rows = await cursor.fetchall()
            total_cursor = await db.execute("SELECT COUNT(*) FROM generations WHERE user_type='internal' AND user_id = ?", (user_filter,))
            total = (await total_cursor.fetchone())[0]
        elif user_role == "admin":
            cursor = await db.execute(
                "SELECT * FROM generations WHERE user_type='internal' ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, (page-1)*per_page)
            )
            rows = await cursor.fetchall()
            total_cursor = await db.execute("SELECT COUNT(*) FROM generations WHERE user_type='internal'")
            total = (await total_cursor.fetchone())[0]
        else:
            cursor = await db.execute(
                "SELECT * FROM generations WHERE user_type='internal' AND user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (current_user_id, per_page, (page-1)*per_page)
            )
            rows = await cursor.fetchall()
            total_cursor = await db.execute("SELECT COUNT(*) FROM generations WHERE user_type='internal' AND user_id = ?", (current_user_id,))
            total = (await total_cursor.fetchone())[0]
    finally:
        await db.close()

    items = []
    for row in rows:
        item = dict(row)
        item["input_image_url"] = f"/generated/internal/{item['user_id']}/{os.path.basename(item['input_image_path'])}" if item['input_image_path'] else None
        item["output_image_url"] = f"/generated/internal/{item['user_id']}/{os.path.basename(item['output_image_path'])}"
        items.append(item)
    return {"items": items, "total": total, "page": page, "per_page": per_page, "pages": (total + per_page - 1) // per_page}

# ========== Админские эндпоинты ==========
@router.get("/users")
async def get_users(current_user = Depends(get_current_admin)):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, username, role FROM users ORDER BY id")
        rows = await cursor.fetchall()
    finally:
        await db.close()
    return [dict(row) for row in rows]

@router.get("/textures/all")
async def get_all_textures(current_admin = Depends(get_current_admin)):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name, filename, material_type, supplier FROM materials ORDER BY material_type, supplier, name")
        rows = await cursor.fetchall()
    finally:
        await db.close()
    return [dict(row) for row in rows]

@router.post("/users")
async def create_user(user_data: UserCreateModel, current_admin = Depends(get_current_admin)):
    db = await get_db()
    try:
        existing_cursor = await db.execute("SELECT id FROM users WHERE username = ?", (user_data.username,))
        existing = await existing_cursor.fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")
        hashed = get_password_hash(user_data.password)
        await db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", (user_data.username, hashed, user_data.role))
        await db.commit()
    finally:
        await db.close()
    return {"message": f"User {user_data.username} created"}

@router.delete("/users/{user_id}")
async def delete_user(user_id: int, current_admin = Depends(get_current_admin)):
    if user_id == current_admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    db = await get_db()
    try:
        user_cursor = await db.execute("SELECT id, role FROM users WHERE id = ?", (user_id,))
        user = await user_cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if user["role"] == "admin":
            raise HTTPException(status_code=403, detail="Cannot delete another admin")
        gens_cursor = await db.execute("SELECT output_image_path, input_image_path FROM generations WHERE user_type='internal' AND user_id = ?", (str(user_id),))
        gens = await gens_cursor.fetchall()
        for gen in gens:
            if os.path.exists(gen["input_image_path"]):
                os.unlink(gen["input_image_path"])
            if os.path.exists(gen["output_image_path"]):
                os.unlink(gen["output_image_path"])
        await db.execute("DELETE FROM generations WHERE user_type='internal' AND user_id = ?", (str(user_id),))
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()
    user_folder = os.path.join(GENERATED_DIR, str(user_id))
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder)
    return {"message": "User deleted"}

@router.get("/generations/all")
async def get_all_generations(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user_type: str = Query(None),
    user_id: str = Query(None),
    current_admin = Depends(get_current_admin),
):
    db = await get_db()
    try:
        query = "SELECT * FROM generations WHERE 1=1"
        params = []
        if user_type:
            query += " AND user_type = ?"
            params.append(user_type)
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([per_page, (page-1)*per_page])
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        total_query = "SELECT COUNT(*) FROM generations WHERE 1=1"
        if user_type:
            total_query += " AND user_type = ?"
        if user_id:
            total_query += " AND user_id = ?"
        total_cursor = await db.execute(total_query, params[:len(params)-2])
        total = (await total_cursor.fetchone())[0]
    finally:
        await db.close()
    items = []
    for row in rows:
        item = dict(row)
        if item["user_type"] == "client":
            item["output_image_url"] = f"/generated/client/{os.path.basename(item['output_image_path'])}"
        else:
            item["output_image_url"] = f"/generated/internal/{item['user_id']}/{os.path.basename(item['output_image_path'])}"
        items.append(item)
    return {"items": items, "total": total, "page": page, "per_page": per_page, "pages": (total + per_page - 1) // per_page}

class DeleteGenerationsRequest(BaseModel):
    ids: List[int]

@router.delete("/generations", dependencies=[Depends(get_current_admin)])
async def delete_generations(req: DeleteGenerationsRequest):
    if not req.ids:
        return {"deleted": 0}
    db = await get_db()
    try:
        placeholders = ",".join("?" * len(req.ids))
        cursor = await db.execute(
            f"SELECT id, input_image_path, output_image_path FROM generations WHERE id IN ({placeholders})",
            req.ids,
        )
        rows = await cursor.fetchall()
        for row in rows:
            for path in [row["input_image_path"], row["output_image_path"]]:
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except Exception as e:
                        logger.warning("Не удалось удалить файл %s: %s", path, e)
        await db.execute(
            f"DELETE FROM generations WHERE id IN ({placeholders})",
            req.ids,
        )
        await db.commit()
    finally:
        await db.close()
    return {"deleted": len(rows)}

# ========== Управление цветами затирки ==========
@router.get("/grout-colors")
async def get_grout_colors():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, name, hex_code FROM grout_colors ORDER BY id")
        rows = await cursor.fetchall()
    finally:
        await db.close()
    return [dict(row) for row in rows]

@router.post("/grout-colors")
async def add_grout_color(data: GroutColorCreate, current_admin = Depends(get_current_admin)):
    db = await get_db()
    try:
        await db.execute("INSERT INTO grout_colors (name, hex_code) VALUES (?, ?)", (data.name, data.hex_code))
        await db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Color already exists")
    finally:
        await db.close()
    return {"message": "Color added"}

@router.delete("/grout-colors/{color_id}")
async def delete_grout_color(color_id: int, current_admin = Depends(get_current_admin)):
    db = await get_db()
    try:
        color_cursor = await db.execute("SELECT id FROM grout_colors WHERE id = ?", (color_id,))
        color = await color_cursor.fetchone()
        if not color:
            raise HTTPException(status_code=404, detail="Color not found")
        await db.execute("DELETE FROM grout_colors WHERE id = ?", (color_id,))
        await db.commit()
    finally:
        await db.close()
    return {"message": "Color deleted"}

# ========== Управление текстурами ==========
@router.get("/suppliers")
async def get_suppliers(material_type: str, current_user = Depends(get_current_manager)):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT DISTINCT supplier FROM materials WHERE material_type = ?", (material_type,))
        rows = await cursor.fetchall()
    finally:
        await db.close()
    name_map = {"redstone": "Redstone", "redstone_premium": "Redstone Premium", "krasny_kamen": "Красный Камень", "reika": "Рейка", "solid": "Сплошные"}
    # Для ригеля порядок: Красный камень → Redstone → Redstone Premium
    sort_order = {"krasny_kamen": 0, "redstone": 1, "redstone_premium": 2}
    suppliers = [{"code": row["supplier"], "name": name_map.get(row["supplier"], row["supplier"])} for row in rows]
    suppliers.sort(key=lambda s: sort_order.get(s["code"], 99))
    return suppliers

@router.get("/textures")
async def get_internal_textures(
    material_type: str,
    supplier: str,
    current_user = Depends(get_current_manager),
):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT name, filename FROM materials WHERE material_type = ? AND supplier = ?",
            (material_type, supplier)
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()
    return [{"name": row["name"], "url": f"/textures/{material_type}/{supplier}/{row['filename']}"} for row in rows]

@router.post("/textures")
async def add_texture(
    name: str = Form(...),
    material_type: str = Form(...),
    supplier: str = Form(...),
    file: UploadFile = File(...),
    current_admin = Depends(get_current_admin),
):
    if material_type not in ("standard", "rigel", "riegel_mixed", "cobblestone", "rubble_stone", "derbent_stone", "flat_stone", "textured_stone", "reika", "solid"):
        raise HTTPException(status_code=400, detail="Invalid material_type")
    if supplier not in ("redstone", "redstone_premium", "krasny_kamen", "reika", "solid"):
        raise HTTPException(status_code=400, detail="Invalid supplier")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Ожидается изображение")
    tex_bytes = await file.read()
    if len(tex_bytes) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл текстуры превышает 15 МБ")
    filename = os.path.basename(file.filename or "upload.jpg")
    # GenAPI валидирует расширение case-sensitive (.PNG → "Неверный формат") — нормализуем.
    stem, ext = os.path.splitext(filename)
    filename = stem + ext.lower()
    save_path = os.path.join("textures", material_type, supplier, filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(tex_bytes)
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO materials (name, filename, material_type, supplier) VALUES (?, ?, ?, ?)",
            (name, filename, material_type, supplier)
        )
        await db.commit()
    except sqlite3.IntegrityError as e:
        msg = "Material already exists" if "UNIQUE" in str(e) else f"DB constraint error: {e}"
        raise HTTPException(status_code=400, detail=msg)
    finally:
        await db.close()
    return {"message": "Material added"}

@router.delete("/textures/{material_id}")
async def delete_texture(material_id: int, current_admin = Depends(get_current_admin)):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT filename, material_type, supplier FROM materials WHERE id = ?", (material_id,))
        material = await cursor.fetchone()
        if not material:
            raise HTTPException(status_code=404, detail="Not found")
        filepath = os.path.join("textures", material["material_type"], material["supplier"], material["filename"])
        if os.path.exists(filepath):
            os.unlink(filepath)
        await db.execute("DELETE FROM materials WHERE id = ?", (material_id,))
        await db.commit()
    finally:
        await db.close()
    return {"message": "Deleted"}


# ============================================================
#  LEADS
# ============================================================
@router.get("/leads")
async def get_leads(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_admin = Depends(get_current_admin),
):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM leads ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (per_page, (page - 1) * per_page),
        )
        rows = await cursor.fetchall()
        total_cur = await db.execute("SELECT COUNT(*) FROM leads")
        total = (await total_cur.fetchone())[0]
    finally:
        await db.close()
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "pages": (total + per_page - 1) // per_page,
    }


# ============================================================
#  NOTIFICATIONS
# ============================================================
@router.get("/notifications")
async def get_notifications(current_admin = Depends(get_current_admin)):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT 50"
        )
        rows = await cur.fetchall()
        unread_cur = await db.execute(
            "SELECT COUNT(*) FROM notifications WHERE is_read = 0"
        )
        unread = (await unread_cur.fetchone())[0]
    finally:
        await db.close()
    return {"unread_count": unread, "notifications": [dict(r) for r in rows]}


@router.post("/notifications/{notif_id}/read")
async def mark_notification_read(notif_id: int, current_admin = Depends(get_current_admin)):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ?", (notif_id,)
        )
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}


@router.post("/notifications/read-all")
async def mark_all_notifications_read(current_admin = Depends(get_current_admin)):
    db = await get_db()
    try:
        await db.execute("UPDATE notifications SET is_read = 1")
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}


# ============================================================
#  ZONE GENERATION (Пояса / Цоколь)
# ============================================================
def _resolve_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "https://rstone.tech").rstrip("/")


async def _run_zone_generation(
    request_id: str,
    image_urls: list,
    prompt: str,
    user_id: str,
    service_type: str,
    texture_name: str,
    temp_path: str,
    material_type: str,
    supplier: str,
    model: str | None = None,
):
    """Фоновая задача генерации для Поясов / Цоколя."""
    try:
        logger.info(
            "Zone generation start request_id=%s service=%s num_urls=%d model=%s",
            request_id, service_type, len(image_urls), model,
        )
        if model:
            result = await generate_image(image_urls, prompt, material_type=material_type, model_override=model)
        else:
            # Цоколь и пояса всегда GPT Image 2 — лучше понимает геометрические команды и маску зоны
            effective_material_type = None if service_type in ("plinth", "belt") else material_type
            result = await generate_image(image_urls, prompt, material_type=effective_material_type)
        output_url = result["output_url"]

        output_dir = os.path.join(GENERATED_DIR, user_id)
        os.makedirs(output_dir, exist_ok=True)
        out_filename = f"{uuid.uuid4()}.jpg"
        out_path = os.path.join(output_dir, out_filename)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(output_url)
            with open(out_path, "wb") as f:
                f.write(resp.content)

        result_url = f"/generated/internal/{user_id}/{out_filename}"
        redis_client.setex(f"gen_status:{request_id}", 3600, f"success:{result_url}")

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO generations (user_type, user_id, input_image_path, output_image_path, "
                "prompt, texture_name, category, material_type, supplier, model_used) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("internal", user_id, temp_path, out_path, prompt,
                 texture_name, service_type, material_type, supplier, model),
            )
            await db.commit()
        finally:
            await db.close()

    except Exception as e:
        logger.error("Zone generation failed request_id=%s: %s", request_id, e)
        redis_client.setex(f"gen_status:{request_id}", 3600, "error")


@router.post("/accent/generate")
async def accent_generate(
    annotated_photo: UploadFile = File(...),
    accent_type: str = Form("plinth"),        # plinth | reika | belt
    orientation: str = Form("horizontal"),    # для reika: horizontal | vertical
    belt_mode: str = Form("soldier"),         # для belt без текстуры: soldier | chess
    cornice: str = Form("no"),                # для belt: yes | no — наличие карниза
    texture: str = Form(None),               # опционально для belt
    material_type: str = Form(None),
    supplier: str = Form(None),
    model: str = Form(None),
    current_user = Depends(get_current_manager),
):
    """Генерация акцентов: Цоколь / Рейка / Пояса."""
    if accent_type not in ("plinth", "reika", "belt"):
        raise HTTPException(status_code=400, detail="accent_type must be plinth, reika or belt")
    if not (annotated_photo.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Ожидается изображение")

    request_id = str(uuid.uuid4())
    user_id    = str(current_user.get("id"))

    filename  = f"{uuid.uuid4()}.jpg"
    temp_path = os.path.join(TEMP_DIR, filename)
    content   = await annotated_photo.read()
    if len(content) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Файл превышает 15 МБ")
    with open(temp_path, "wb") as f:
        f.write(content)

    base_url  = _resolve_base_url()
    photo_url = f"{base_url}/temp/internal/{filename}"

    image_urls   = [photo_url]
    texture_name = ""

    # Добавляем текстуру если передана (обязательна для plinth/reika, опциональна для belt)
    if texture and material_type and supplier:
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT filename FROM materials WHERE name = ? AND material_type = ? AND supplier = ?",
                (texture, material_type, supplier),
            )
            row = await cur.fetchone()
        finally:
            await db.close()
        if not row:
            raise HTTPException(status_code=404, detail=f"Текстура «{texture}» не найдена")
        texture_url = f"{base_url}/textures/{material_type}/{supplier}/{quote(row['filename'])}"
        image_urls.append(texture_url)
        texture_name = texture

    # Выбираем промт по типу подвкладки
    has_texture = len(image_urls) > 1
    if accent_type == "plinth":
        if not has_texture:
            raise HTTPException(status_code=400, detail="Для Цоколя необходимо выбрать текстуру")
        prompt = build_plinth_prompt(material_type)
    elif accent_type == "reika":
        if not has_texture:
            raise HTTPException(status_code=400, detail="Для Рейки необходимо выбрать текстуру")
        prompt = build_reika_prompt(orientation)
    else:  # belt
        prompt = build_belt_prompt(has_texture, material_type, belt_mode, cornice)

    redis_client.setex(f"gen_status:{request_id}", 3600, "processing")
    asyncio.create_task(_run_zone_generation(
        request_id, image_urls, prompt,
        user_id, accent_type, texture_name, temp_path,
        material_type or "", supplier or "",
        model=model,
    ))
    return {"request_id": request_id}


# ============================================================
#  FREE PROMPTS — re-generation with custom prompt
# ============================================================

async def _run_free_generation(
    request_id: str, user_id: str, image_url: str,
    prompt: str, source_id: int, model: str | None,
):
    try:
        logger.info("free_generate bg start request_id=%s user_id=%s model=%s", request_id, user_id, model)
        result = await generate_image([image_url], prompt, model_override=model)
        output_url = result["output_url"]

        output_dir = os.path.join(GENERATED_DIR, user_id)
        os.makedirs(output_dir, exist_ok=True)
        out_filename = f"{uuid.uuid4().hex}.jpg"
        out_path = os.path.join(output_dir, out_filename)

        async with httpx.AsyncClient(timeout=60.0) as dl_client:
            img_resp = await dl_client.get(output_url)
            img_resp.raise_for_status()
            with open(out_path, "wb") as f:
                f.write(img_resp.content)

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO generations (user_type, user_id, input_image_path, output_image_path, "
                "prompt, category, model_used) VALUES (?, ?, ?, ?, ?, 'free', ?)",
                ("internal", user_id, f"source:{source_id}", out_path, prompt, model),
            )
            await db.commit()
        finally:
            await db.close()

        result_url = f"/generated/internal/{user_id}/{out_filename}"
        redis_client.setex(f"gen_status:{request_id}", 3600, f"success:{result_url}")
        logger.info("free_generate bg success request_id=%s", request_id)
    except Exception as e:
        logger.error("free_generate bg error request_id=%s: %s", request_id, e)
        redis_client.setex(f"gen_status:{request_id}", 3600, "error")


class FreeGenerateRequest(BaseModel):
    source_generation_id: int
    custom_prompt: str
    model: str | None = None


@router.post("/free-generate")
async def free_generate(data: FreeGenerateRequest, request: Request, current_user=Depends(get_current_manager)):
    user_id = str(current_user["id"])
    user_role = current_user["role"]

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, user_id, output_image_path FROM generations WHERE id = ? AND user_type = 'internal'",
            (data.source_generation_id,),
        )
        row = await cursor.fetchone()
    finally:
        await db.close()

    if not row:
        raise HTTPException(404, "Генерация не найдена")
    if user_role != "admin" and str(row["user_id"]) != user_id:
        raise HTTPException(403, "Нет доступа к этой генерации")

    base_url = resolve_public_base_url(request)
    owner_id = str(row["user_id"])
    output_filename = os.path.basename(row["output_image_path"])
    image_url = f"{base_url}/generated/internal/{owner_id}/{output_filename}"

    request_id = str(uuid.uuid4())
    redis_client.setex(f"gen_status:{request_id}", 3600, "processing")
    asyncio.create_task(_run_free_generation(
        request_id, user_id, image_url,
        data.custom_prompt, data.source_generation_id, data.model,
    ))
    return {"request_id": request_id}


# ============================================================
#  CHAT — text analysis via GenAPI GPT-4o
# ============================================================

CHAT_ENDPOINT = "https://proxy.gen-api.ru/v1/chat/completions"
CHAT_MODEL = "gpt-5-5"
CHAT_SYSTEM_PROMPT = (
    "Ты эксперт по облицовке зданий натуральным камнем и кирпичом. "
    "Анализируй изображения визуализации и давай рекомендации по улучшению. "
    "Отвечай на русском языке, кратко и по делу."
)


def _extract_chat_content(result) -> str:
    """Pull assistant text from either OpenAI (choices) or GenAPI (result list) format."""
    if isinstance(result, list) and result:
        item = result[0]
        if isinstance(item, dict):
            return item.get("message", {}).get("content", "") or str(item)
        return str(item)
    if isinstance(result, dict):
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            return choices[0].get("message", {}).get("content", "") or ""
        for key in ("result", "output", "full_response"):
            val = result.get(key)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                content = val[0].get("message", {}).get("content")
                if content:
                    return content
            if isinstance(val, str) and val:
                return val
        return result.get("content") or result.get("text") or ""
    return str(result)


async def _chat_completion(api_key: str, messages: list, max_tokens: int = 1024) -> str:
    """Call the OpenAI-compatible GenAPI proxy (synchronous, standard format)."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {"model": CHAT_MODEL, "messages": messages, "max_tokens": max_tokens}
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=5.0)) as client:
        resp = await client.post(CHAT_ENDPOINT, json=payload, headers=headers)
        if not resp.is_success:
            logger.error("Chat API error status=%s body=%s", resp.status_code, resp.text[:500])
            raise HTTPException(502, "Ошибка текстовой модели")
        result = resp.json()
    return _extract_chat_content(result)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    image_url: str | None = None
    history: list[ChatMessage] = []


@router.post("/chat")
async def chat_analysis(data: ChatRequest, request: Request, current_user=Depends(get_current_manager)):
    api_key = os.getenv("GEN_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise HTTPException(500, "API ключ не настроен")

    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    for msg in data.history:
        messages.append({"role": msg.role, "content": msg.content})

    user_content = data.message
    if data.image_url:
        base_url = resolve_public_base_url(request)
        full_url = data.image_url if data.image_url.startswith("http") else f"{base_url}{data.image_url}"
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": full_url}},
                {"type": "text", "text": user_content},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_content})

    reply = await _chat_completion(api_key, messages)
    return {"reply": reply}


# ============================================================
#  AI SYSTEM PROMPT GENERATOR
# ============================================================

PROMPT_GEN_SYSTEM = (
    "Ты — эксперт по облицовке зданий натуральным камнем, кирпичом и декоративными материалами. "
    "Пользователь предоставит фотографии текстуры материала и/или фото реальной кладки. "
    "Твоя задача — проанализировать материал и написать детальный системный промт на АНГЛИЙСКОМ языке "
    "для AI-модели генерации изображений, которая будет заменять облицовку зданий на этот материал.\n\n"
    "Системный промт должен содержать:\n"
    "1. Описание физических свойств материала (форма, размер, текстура поверхности)\n"
    "2. Характерные особенности укладки (паттерн, швы, расстояние между элементами)\n"
    "3. Цветовую палитру и вариации\n"
    "4. Особенности отражения света и теней\n"
    "5. Важные детали для реалистичной генерации\n\n"
    "Формат ответа: ТОЛЬКО текст системного промта, без пояснений, заголовков и кавычек. "
    "Промт должен быть 3-6 предложений, начинаться с описания типа материала. "
    "Пиши на английском языке."
)


@router.post("/generate-system-prompt")
async def generate_system_prompt(
    request: Request,
    texture_photo: UploadFile = File(None),
    real_photo: UploadFile = File(None),
    description: str = Form(""),
    current_user=Depends(get_current_admin),
):
    api_key = os.getenv("GEN_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise HTTPException(500, "API ключ не настроен")

    if not texture_photo and not real_photo and not description:
        raise HTTPException(400, "Загрузите хотя бы одно фото или добавьте описание")

    base_url = resolve_public_base_url(request)
    temp_files = []
    image_parts = []

    for label, upload in [("Текстура материала", texture_photo), ("Реальная кладка", real_photo)]:
        if not upload:
            continue
        contents = await upload.read()
        if len(contents) > 15 * 1024 * 1024:
            raise HTTPException(413, f"Файл {label} превышает 15 МБ")
        fname = f"{uuid.uuid4().hex}.jpg"
        fpath = os.path.join(TEMP_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(contents)
        temp_files.append(fpath)
        url = f"{base_url}/temp/internal/{fname}"
        image_parts.append({"type": "image_url", "image_url": {"url": url}})

    user_text = "Проанализируй этот облицовочный материал и составь системный промт для AI-генерации."
    if description.strip():
        user_text += f"\n\nДополнительное описание от администратора: {description.strip()}"

    user_content: list | str = image_parts + [{"type": "text", "text": user_text}] if image_parts else user_text

    messages = [
        {"role": "system", "content": PROMPT_GEN_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    try:
        reply = await _chat_completion(api_key, messages)
        return {"system_prompt": reply.strip()}
    finally:
        for fp in temp_files:
            if os.path.exists(fp):
                os.unlink(fp)


# ============================================================
#  CUSTOM MATERIAL TYPES (admin CRUD)
# ============================================================
_BUILTIN_MATERIAL_TYPES = [
    {"slug": "standard",       "display_name": "Кирпич",                "builtin": True},
    {"slug": "rigel",          "display_name": "Ригель",                 "builtin": True},
    {"slug": "riegel_mixed",   "display_name": "Разноформатный ригель",  "builtin": True},
    {"slug": "cobblestone",    "display_name": "Круглый камень",         "builtin": True},
    {"slug": "rubble_stone",   "display_name": "Рваный камень",          "builtin": True},
    {"slug": "flat_stone",     "display_name": "Плоский камень",         "builtin": True},
    {"slug": "textured_stone", "display_name": "Фактурный камень",       "builtin": True},
    {"slug": "solid",          "display_name": "Сплошные",               "builtin": True},
    {"slug": "reika",          "display_name": "Рейка",                  "builtin": True},
]


class CustomMaterialTypeCreate(BaseModel):
    slug: str
    display_name: str
    system_prompt: str
    default_model: str = "gpt-image-2"


class CustomMaterialTypeUpdate(BaseModel):
    display_name: str | None = None
    system_prompt: str | None = None
    default_model: str | None = None


@router.get("/material-types")
async def list_material_types(current_user=Depends(get_current_manager)):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, slug, display_name, system_prompt, default_model, created_at "
            "FROM custom_material_types ORDER BY display_name"
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()
    custom = [dict(row) | {"builtin": False} for row in rows]
    return _BUILTIN_MATERIAL_TYPES + custom


@router.post("/material-types")
async def create_material_type(data: CustomMaterialTypeCreate, current_user=Depends(get_current_admin)):
    builtin_slugs = {t["slug"] for t in _BUILTIN_MATERIAL_TYPES}
    if data.slug in builtin_slugs:
        raise HTTPException(400, "Этот slug зарезервирован для встроенного типа")
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO custom_material_types (slug, display_name, system_prompt, default_model) "
            "VALUES (?, ?, ?, ?)",
            (data.slug, data.display_name, data.system_prompt, data.default_model),
        )
        await db.commit()
        cursor = await db.execute("SELECT last_insert_rowid()")
        row = await cursor.fetchone()
        return {"id": row[0], "slug": data.slug}
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(400, f"Тип с slug '{data.slug}' уже существует")
        raise
    finally:
        await db.close()


@router.put("/material-types/{type_id}")
async def update_material_type(type_id: int, data: CustomMaterialTypeUpdate, current_user=Depends(get_current_admin)):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM custom_material_types WHERE id = ?", (type_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, "Тип не найден")
        updates, params = [], []
        if data.display_name is not None:
            updates.append("display_name = ?"); params.append(data.display_name)
        if data.system_prompt is not None:
            updates.append("system_prompt = ?"); params.append(data.system_prompt)
        if data.default_model is not None:
            updates.append("default_model = ?"); params.append(data.default_model)
        if not updates:
            raise HTTPException(400, "Нечего обновлять")
        params.append(type_id)
        await db.execute(f"UPDATE custom_material_types SET {', '.join(updates)} WHERE id = ?", params)
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}


@router.delete("/material-types/{type_id}")
async def delete_material_type(type_id: int, current_user=Depends(get_current_admin)):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM custom_material_types WHERE id = ?", (type_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, "Тип не найден")
        await db.execute("DELETE FROM custom_material_types WHERE id = ?", (type_id,))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}


# ============================================================
#  PROMPT TEMPLATES (admin CRUD, read for all managers)
# ============================================================

class PromptTemplateCreate(BaseModel):
    category: str  # 'fix' or 'style'
    label: str
    prompt_text: str
    sort_order: int = 0


@router.get("/prompt-templates")
async def list_prompt_templates(current_user=Depends(get_current_manager)):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, category, label, prompt_text, sort_order FROM prompt_templates ORDER BY category, sort_order, id"
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()
    return [dict(row) for row in rows]


@router.post("/prompt-templates")
async def create_prompt_template(data: PromptTemplateCreate, current_user=Depends(get_current_admin)):
    if data.category not in ("fix", "style"):
        raise HTTPException(400, "category must be 'fix' or 'style'")
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO prompt_templates (category, label, prompt_text, sort_order) VALUES (?, ?, ?, ?)",
            (data.category, data.label, data.prompt_text, data.sort_order),
        )
        await db.commit()
        cursor = await db.execute("SELECT last_insert_rowid()")
        row = await cursor.fetchone()
        return {"id": row[0]}
    finally:
        await db.close()


@router.delete("/prompt-templates/{template_id}")
async def delete_prompt_template(template_id: int, current_user=Depends(get_current_admin)):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM prompt_templates WHERE id = ?", (template_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, "Шаблон не найден")
        await db.execute("DELETE FROM prompt_templates WHERE id = ?", (template_id,))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True}