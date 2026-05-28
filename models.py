from pydantic import BaseModel
from typing import Optional

class UserCreate(BaseModel):
    username: str
    password: str
    role: str  # 'manager' или 'admin'

class UserLogin(BaseModel):
    username: str
    password: str

class UserOut(BaseModel):
    id: int
    username: str
    role: str

class GenerationOut(BaseModel):
    id: int
    user_type: str
    user_id: str
    input_image_path: str
    output_image_path: str
    prompt: Optional[str]
    texture_name: Optional[str]
    grout_color: Optional[str]
    category: Optional[str]
    material_type: Optional[str]
    supplier: Optional[str]
    model_used: Optional[str]
    created_at: str