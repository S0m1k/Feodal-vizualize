import os
import uuid
import shutil
import httpx
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, UploadFile, Form, File, Query, Request
from database import get_db
from middleware import get_current_manager, get_current_user, get_current_admin
import asyncio
from utils.generation import generate_image, build_prompt, build_belt_prompt, build_plinth_prompt, build_reika_prompt
from utils.notifications import create_notification
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
redis_client = Redis(host='localhost', port=6379, decode_responses=True)


def resolve_public_base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


# ===== Модели =====
class UserCreateModel(BaseModel):
    username: str
    password: str
    role: str = "manager"

class GroutColorCreate(BaseModel):
    name: str
    hex_code: str

# ========== Генерация для сотрудников ==========
@router.post("/generate")
async def internal_generate(
    request: Request,
    file: UploadFile,
    texture: str = Form(...),
    category: str = Form("facade"),
    material_type: str = Form(...),
    supplier: str = Form(...),
    grout_color_name: str = Form(None),
    current_user = Depends(get_current_manager),
):
    logger.info(
        "internal_generate start user_id=%s material_type=%s supplier=%s texture=%s category=%s grout=%s",
        current_user.get("id"),
        material_type,
        supplier,
        texture,
        category,
        grout_color_name,
    )
    temp_filename = f"{uuid.uuid4().hex}.jpg"
    temp_path = os.path.join(TEMP_DIR, temp_filename)
    print(">>> internal_generate called")
    with open(temp_path, "wb") as f:
        f.write(await file.read())

    try:
        base_url = resolve_public_base_url(request)
        host = (request.url.hostname or "").lower()
        if host in ("localhost", "127.0.0.1", "::1") and not os.getenv("PUBLIC_BASE_URL"):
            logger.warning(
                "internal_generate: GenAPI скачивает image_urls с вашего сервера. "
                "С localhost это недоступно извне — задайте PUBLIC_BASE_URL (ngrok/cloudflare tunnel/VPS) "
                "или генерация по ссылкам не сработает."
            )
        photo_url = f"{base_url}/temp/internal/{temp_filename}"

        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT filename FROM materials WHERE name = ? AND material_type = ? AND supplier = ?",
                (texture, material_type, supplier)
            )
            row = await cursor.fetchone()
        finally:
            await db.close()

        if not row:
            raise HTTPException(status_code=404, detail="Текстура не найдена")
        filename = row["filename"]
        texture_url = f"{base_url}/textures/{material_type}/{supplier}/{quote(filename)}"

        grout_hex = None
        if grout_color_name and material_type != "decorative_stone":
            db = await get_db()
            try:
                grout_cursor = await db.execute(
                    "SELECT hex_code FROM grout_colors WHERE name = ?",
                    (grout_color_name,)
                )
                grout_row = await grout_cursor.fetchone()
            finally:
                await db.close()
            if grout_row:
                grout_hex = grout_row["hex_code"]

        prompt = build_prompt(category, material_type, grout_hex)

        try:
            result_data = await generate_image([photo_url, texture_url], prompt)
        except Exception as e:
            logger.error("internal_generate ai_error user_id=%s error=%s", current_user.get("id"), e)
            raise HTTPException(status_code=502, detail=f"Ошибка AI API: {e}")

        output_url = result_data.get("output_url")
        if not output_url:
            raise HTTPException(status_code=500, detail="Ошибка генерации: пустой output_url")

        try:
            async with httpx.AsyncClient(timeout=60.0) as dl_client:
                img_resp = await dl_client.get(output_url)
                img_resp.raise_for_status()
                result_bytes = img_resp.content
        except Exception as e:
            logger.error("internal_generate download_error output_url=%s error=%s", output_url, e)
            raise HTTPException(status_code=502, detail=f"Не удалось скачать результат: {e}")

        user_id = str(current_user["id"])
        user_output_dir = os.path.join(GENERATED_DIR, user_id)
        os.makedirs(user_output_dir, exist_ok=True)
        output_filename = f"{uuid.uuid4().hex}.jpg"
        output_path = os.path.join(user_output_dir, output_filename)
        with open(output_path, "wb") as f:
            f.write(result_bytes)

        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO generations (user_type, user_id, input_image_path, output_image_path, prompt, texture_name, grout_color, category, material_type, supplier) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("internal", user_id, temp_path, output_path, prompt, texture, grout_color_name, category, material_type, supplier)
            )
            await db.commit()
        finally:
            await db.close()

        request_id = str(uuid.uuid4())
        result_url = f"/generated/internal/{user_id}/{output_filename}"
        redis_client.setex(f"gen_status:{request_id}", 300, f"success:{result_url}")
        logger.info(
            "internal_generate success user_id=%s request_id=%s result_url=%s",
            user_id,
            request_id,
            result_url,
        )
        return {"request_id": request_id, "result_url": result_url}
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
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
    name_map = {"redstone": "Redstone", "redstone_premium": "Redstone Premium", "krasny_kamen": "Красный Камень", "reika": "Рейка"}
    return [{"code": row["supplier"], "name": name_map.get(row["supplier"], row["supplier"])} for row in rows]

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
    if material_type not in ("standard", "rigel", "decorative_stone", "reika"):
        raise HTTPException(status_code=400, detail="Invalid material_type")
    if supplier not in ("redstone", "redstone_premium", "krasny_kamen", "reika"):
        raise HTTPException(status_code=400, detail="Invalid supplier")
    filename = file.filename
    save_path = os.path.join("textures", material_type, supplier, filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(await file.read())
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
):
    """Фоновая задача генерации для Поясов / Цоколя."""
    try:
        logger.info(
            "Zone generation start request_id=%s service=%s num_urls=%d",
            request_id, service_type, len(image_urls),
        )
        result = await generate_image(image_urls, prompt)
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
                "prompt, texture_name, category, material_type, supplier) VALUES (?,?,?,?,?,?,?,?,?)",
                ("internal", user_id, temp_path, out_path, prompt,
                 texture_name, service_type, material_type, supplier),
            )
            await db.commit()
        finally:
            await db.close()

    except Exception as e:
        logger.error("Zone generation failed request_id=%s: %s", request_id, e)
        redis_client.setex(f"gen_status:{request_id}", 3600, "error")


@router.post("/belt/generate")
async def belt_generate(
    annotated_photo: UploadFile = File(...),
    category: str = Form("facade"),
    mode: str = Form("soldier"),
    current_user = Depends(get_current_manager),
):
    """Генерация пояса — текстура не нужна, работаем только с аннотированным фото.
    mode: 'soldier' (вертикальный ряд) | 'checker' (шахматный порядок)
    """
    request_id = str(uuid.uuid4())
    user_id    = str(current_user.get("id"))

    filename  = f"{uuid.uuid4()}.jpg"
    temp_path = os.path.join(TEMP_DIR, filename)
    content   = await annotated_photo.read()
    with open(temp_path, "wb") as f:
        f.write(content)

    base_url  = _resolve_base_url()
    photo_url = f"{base_url}/temp/internal/{filename}"
    prompt    = build_belt_prompt(category, mode)

    redis_client.setex(f"gen_status:{request_id}", 3600, "processing")
    asyncio.create_task(_run_zone_generation(
        request_id, [photo_url], prompt,
        user_id, "belt", "", temp_path, "", "",
    ))
    return {"request_id": request_id}


@router.post("/plinth/generate")
async def plinth_generate(
    annotated_photo: UploadFile = File(...),
    texture: str = Form(...),
    material_type: str = Form(...),
    supplier: str = Form(...),
    current_user = Depends(get_current_manager),
):
    request_id = str(uuid.uuid4())
    user_id    = str(current_user.get("id"))

    # Resolve the actual filename from DB (texture form field is the display name)
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

    filename  = f"{uuid.uuid4()}.jpg"
    temp_path = os.path.join(TEMP_DIR, filename)
    content   = await annotated_photo.read()
    with open(temp_path, "wb") as f:
        f.write(content)

    base_url    = _resolve_base_url()
    photo_url   = f"{base_url}/temp/internal/{filename}"
    texture_url = f"{base_url}/textures/{material_type}/{supplier}/{quote(row['filename'])}"
    prompt      = build_plinth_prompt()

    redis_client.setex(f"gen_status:{request_id}", 3600, "processing")
    asyncio.create_task(_run_zone_generation(
        request_id, [photo_url, texture_url], prompt,
        user_id, "plinth", texture, temp_path, material_type, supplier,
    ))
    return {"request_id": request_id}


@router.post("/reika/generate")
async def reika_generate(
    annotated_photo: UploadFile = File(...),
    texture: str = Form(...),
    material_type: str = Form("reika"),
    supplier: str = Form("reika"),
    current_user = Depends(get_current_manager),
):
    """Генерация рейки — пользователь обводит зону красным, выбирает текстуру рейки."""
    request_id = str(uuid.uuid4())
    user_id    = str(current_user.get("id"))

    # Resolve actual filename from DB
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

    filename  = f"{uuid.uuid4()}.jpg"
    temp_path = os.path.join(TEMP_DIR, filename)
    content   = await annotated_photo.read()
    with open(temp_path, "wb") as f:
        f.write(content)

    base_url    = _resolve_base_url()
    photo_url   = f"{base_url}/temp/internal/{filename}"
    texture_url = f"{base_url}/textures/{material_type}/{supplier}/{quote(row['filename'])}"
    prompt      = build_reika_prompt()

    redis_client.setex(f"gen_status:{request_id}", 3600, "processing")
    asyncio.create_task(_run_zone_generation(
        request_id, [photo_url, texture_url], prompt,
        user_id, "reika", texture, temp_path, material_type, supplier,
    ))
    return {"request_id": request_id}