from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
import asyncio
import os
from dotenv import load_dotenv
from routers import auth, internal, client
from database import init_db_sync, get_db
from redis import Redis
from utils.balance_checker import balance_checker_loop

load_dotenv()
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
app = FastAPI()
redis_client = Redis(
    host='localhost', port=6379, decode_responses=True,
    password=os.getenv("REDIS_PASSWORD") or None,
)

# Публичные клиентские пути — разрешаем любой Origin (без credentials)
_PUBLIC_CLIENT_PATHS = (
    "/api/client/suppliers",
    "/api/client/textures",
    "/api/client/grout-colors",
    "/status/",
)

@app.middleware("http")
async def public_cors_middleware(request: Request, call_next):
    origin = request.headers.get("origin", "")
    is_public = any(request.url.path.startswith(p) for p in _PUBLIC_CLIENT_PATHS)

    # Обрабатываем preflight OPTIONS для публичных маршрутов
    if is_public and request.method == "OPTIONS":
        r = Response()
        r.headers["Access-Control-Allow-Origin"] = origin or "*"
        r.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, ngrok-skip-browser-warning"
        r.headers["Access-Control-Max-Age"] = "86400"
        return r

    response = await call_next(request)

    # Добавляем CORS-заголовок для публичных GET-ответов
    if is_public and origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"

    return response

# CORS для авторизованных маршрутов (дашборд, generate, auth)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://rstone.tech",
        "https://www.rstone.tech",
        "https://rstone.ru",
        "https://www.rstone.ru",
        "https://swiftly-natural-sitar.tilda.ws",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


init_db_sync()

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(balance_checker_loop(redis_client))

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(internal.router, prefix="/api/internal", tags=["internal"])
app.include_router(client.router, prefix="/api/client", tags=["client"])

# Статические файлы (иконки, прочее)
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Раздача статических файлов (текстур)
app.mount("/textures", StaticFiles(directory="textures"), name="textures")

# Эндпоинты для отдачи временных и сгенерированных файлов
@app.get("/temp/client/{filename}")
def get_temp_client(filename: str):
    safe = os.path.basename(filename)
    filepath = os.path.join("temp/client", safe)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath)

@app.get("/temp/internal/{filename}")
def get_temp_internal(filename: str):
    safe = os.path.basename(filename)
    filepath = os.path.join("temp/internal", safe)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath)

@app.get("/generated/client/{filename}")
def get_generated_client(filename: str):
    safe = os.path.basename(filename)
    filepath = os.path.join("data/generations/client", safe)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath)

@app.get("/generated/internal/{user_id}/{filename}")
def get_generated_internal(user_id: str, filename: str):
    safe_uid = os.path.basename(user_id)
    safe_fn  = os.path.basename(filename)
    filepath = os.path.join("data/generations/internal", safe_uid, safe_fn)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath)

# Эндпоинты для отдачи текстур без префикса /textures (для совместимости с фронтом)
@app.get("/standard/{filename}")
def standard_texture(filename: str):
    safe = os.path.basename(filename)
    filepath = os.path.join("textures", "standard", safe)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath)

@app.get("/rigel/{filename}")
def rigel_texture(filename: str):
    safe = os.path.basename(filename)
    filepath = os.path.join("textures", "rigel", safe)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath)

@app.get("/login")
def login_page():
    return FileResponse("templates/login.html")

@app.get("/dashboard")
def dashboard_page():
    return FileResponse("templates/dashboard.html")

@app.get("/client")
def client_widget():
    return FileResponse("index.html")

@app.get("/")
def root():
    return RedirectResponse(url="/login")

@app.get("/status/{request_id}")
async def get_status(request_id: str):
    data = redis_client.get(f"gen_status:{request_id}")
    if not data:
        raise HTTPException(status_code=404, detail="Request not found")
    if data.startswith("success:"):
        result_url = data.split(":", 1)[1]
        return {"status": "success", "result_url": result_url}
    elif data == "error":
        return {"status": "error"}
    else:
        return {"status": "processing"}

@app.middleware("http")
async def add_cross_origin_resource_policy(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/textures/"):
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    return response

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)
