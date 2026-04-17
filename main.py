from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import os
from dotenv import load_dotenv
from routers import auth, internal, client
from database import init_db_sync
from fastapi import FastAPI, HTTPException, Depends
from database import get_db
from redis import Redis
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request

load_dotenv()
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
app = FastAPI()
redis_client = Redis(host='localhost', port=6379, decode_responses=True)







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

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(internal.router, prefix="/api/internal", tags=["internal"])
app.include_router(client.router, prefix="/api/client", tags=["client"])

# Раздача статических файлов (текстур)
app.mount("/textures", StaticFiles(directory="textures"), name="textures")

# Эндпоинты для отдачи временных и сгенерированных файлов
@app.get("/temp/client/{filename}")
def get_temp_client(filename: str):
    filepath = os.path.join("temp/client", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath, headers={"ngrok-skip-browser-warning": "true"})

@app.get("/temp/internal/{filename}")
def get_temp_internal(filename: str):
    filepath = os.path.join("temp/internal", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath, headers={"ngrok-skip-browser-warning": "true"})

@app.get("/generated/client/{filename}")
def get_generated_client(filename: str):
    filepath = os.path.join("data/generations/client", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath, headers={"ngrok-skip-browser-warning": "true"})

@app.get("/generated/internal/{user_id}/{filename}")
def get_generated_internal(user_id: str, filename: str):
    filepath = os.path.join("data/generations/internal", user_id, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath, headers={"ngrok-skip-browser-warning": "true"})

# Эндпоинты для отдачи текстур без префикса /textures (для совместимости с фронтом)
@app.get("/standard/{filename}")
def standard_texture(filename: str):
    filepath = os.path.join("textures", "standard", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath, headers={"ngrok-skip-browser-warning": "true"})

@app.get("/rigel/{filename}")
def rigel_texture(filename: str):
    filepath = os.path.join("textures", "rigel", filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404)
    return FileResponse(filepath, headers={"ngrok-skip-browser-warning": "true"})

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