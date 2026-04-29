from fastapi import APIRouter, Depends, HTTPException, Response
from rauth import verify_password, create_access_token, get_password_hash
from database import get_db
from middleware import get_current_user
from models import UserLogin, UserOut

router = APIRouter()

@router.post("/login")
async def login(user: UserLogin, response: Response):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, username, password_hash, role FROM users WHERE username = ?",
            (user.username,)
        )
        db_user = await cursor.fetchone()

        if not db_user or not verify_password(user.password, db_user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        # Авто-миграция: если хэш ещё старый SHA-256 — перезаписать на bcrypt
        old_hash = db_user["password_hash"]
        if len(old_hash) == 64 and all(c in "0123456789abcdef" for c in old_hash):
            new_hash = get_password_hash(user.password)
            await db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_hash, db_user["id"]),
            )
            await db.commit()
    finally:
        await db.close()

    token = create_access_token(data={"sub": str(db_user["id"]), "role": db_user["role"]})
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60*60*24
    )
    return {"message": "Login successful"}

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Logged out"}

@router.get("/me")
async def get_me(current_user = Depends(get_current_user)):
    return {"id": current_user["id"], "username": current_user["username"], "role": current_user["role"]}