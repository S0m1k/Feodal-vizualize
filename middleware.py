from fastapi import Depends, HTTPException, Request
from rauth import decode_access_token
from database import get_db_connection

async def get_current_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    db = await get_db_connection()
    try:
        cursor = await db.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,))
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return dict(user)
    finally:
        await db.close()

def get_current_manager(current_user = Depends(get_current_user)):
    if current_user["role"] not in ("manager", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return current_user

def get_current_admin(current_user = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin rights required")
    return current_user