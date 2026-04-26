import aiosqlite
import sqlite3
import os
from rauth import get_password_hash

DATABASE_PATH = "data/app.db"

async def get_db():
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    return db

async def get_db_connection():
    """Возвращает новое соединение с БД (без автоматического закрытия)."""
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    return db

def init_db_sync():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('manager', 'admin')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_type TEXT NOT NULL CHECK(user_type IN ('client', 'internal')),
            user_id TEXT NOT NULL,
            input_image_path TEXT NOT NULL,
            output_image_path TEXT NOT NULL,
            prompt TEXT,
            texture_name TEXT,
            grout_color TEXT,
            category TEXT,
            material_type TEXT,
            supplier TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS grout_colors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            hex_code TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            material_type TEXT NOT NULL CHECK(material_type IN ('standard', 'rigel', 'decorative_stone', 'reika')),
            supplier TEXT NOT NULL CHECK(supplier IN ('redstone', 'redstone_premium', 'krasny_kamen', 'reika')),
            UNIQUE(name, material_type, supplier)
        );
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact TEXT NOT NULL,
            contact_type TEXT NOT NULL CHECK(contact_type IN ('email', 'phone')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Миграция: расширить CHECK-ограничения materials, если 'reika' ещё не включён
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='materials'")
    row = cur.fetchone()
    if row and "'reika'" not in row[0]:
        conn.executescript("""
            ALTER TABLE materials RENAME TO materials_old;
            CREATE TABLE materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filename TEXT NOT NULL,
                material_type TEXT NOT NULL CHECK(material_type IN ('standard', 'rigel', 'decorative_stone', 'reika')),
                supplier TEXT NOT NULL CHECK(supplier IN ('redstone', 'redstone_premium', 'krasny_kamen', 'reika')),
                UNIQUE(name, material_type, supplier)
            );
            INSERT INTO materials SELECT * FROM materials_old;
            DROP TABLE materials_old;
        """)

    # Добавляем начальные цвета затирки
    cur = conn.execute("SELECT COUNT(*) FROM grout_colors")
    if cur.fetchone()[0] == 0:
        default_colors = [
            ("Белый", "#FFFFFF"),
            ("Черный", "#000000"),
            ("Серый", "#808080"),
            ("Коричневый", "#8B4513"),
            ("Бежевый", "#F5F5DC"),
        ]
        for name, hex_code in default_colors:
            conn.execute("INSERT INTO grout_colors (name, hex_code) VALUES (?, ?)", (name, hex_code))

    # Добавляем начальные материалы
    cur = conn.execute("SELECT COUNT(*) FROM materials")
    if cur.fetchone()[0] == 0:
        materials = [
            ("Серый кирпич", "grey.jpg", "standard", "redstone"),
            ("Коричневый кирпич", "brown.jpg", "standard", "redstone"),
            ("Желтый кирпич", "yellow.jpg", "standard", "redstone"),
            ("Серый кирпич", "grey_kr.jpg", "standard", "krasny_kamen"),
            ("Коричневый кирпич", "brown_kr.jpg", "standard", "krasny_kamen"),
            ("Серый ригель", "grey_rigel_prem.jpg", "rigel", "redstone_premium"),
            ("Коричневый ригель", "brown_rigel_prem.jpg", "rigel", "redstone_premium"),
            ("Скала", "rock.jpg", "decorative_stone", "krasny_kamen"),
            ("Сланец", "slate.jpg", "decorative_stone", "krasny_kamen"),
        ]
        for name, filename, mtype, supplier in materials:
            conn.execute("INSERT INTO materials (name, filename, material_type, supplier) VALUES (?, ?, ?, ?)",
                         (name, filename, mtype, supplier))

    # Создаём администратора
    cur = conn.execute("SELECT id FROM users WHERE username = ?", ("admin",))
    if not cur.fetchone():
        pwd_hash = get_password_hash("admin")
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                     ("admin", pwd_hash, "admin"))
        conn.commit()
    conn.close()