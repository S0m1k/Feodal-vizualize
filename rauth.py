import os
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY не задан! Добавьте в .env строку вида: "
        "SECRET_KEY=<случайная строка минимум 32 символа>"
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return _pwd_ctx.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Обратная совместимость: если хэш — старый SHA-256 (64 hex-символа),
    # проверяем по-старому и при успехе автоматически не обновляем
    # (обновление хэша делается в auth.login при необходимости).
    import hashlib
    if len(hashed_password) == 64 and all(c in "0123456789abcdef" for c in hashed_password):
        return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password
    return _pwd_ctx.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None