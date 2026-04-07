import sqlite3
import os
import shutil
from rauth import get_password_hash

DATABASE_PATH = "data/app.db"
BACKUP_PATH = "data/app.db.backup"


def migrate():
    # 1. Бэкап и извлечение admin из старой БД (если есть)
    admin = None
    if os.path.exists(DATABASE_PATH):
        shutil.copy(DATABASE_PATH, BACKUP_PATH)
        print(f"Backup created: {BACKUP_PATH}")

        # Подключаемся к старой БД
        old_conn = sqlite3.connect(DATABASE_PATH)
        old_cursor = old_conn.cursor()

        # Проверяем существование таблицы users
        old_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if old_cursor.fetchone():
            old_cursor.execute("SELECT id, username, password_hash, role FROM users WHERE username = 'admin'")
            admin = old_cursor.fetchone()
        old_conn.close()
    else:
        print("No existing database found. Creating new one.")

    # 2. Создаём новую БД (удаляем старую, если она ещё существует)
    if os.path.exists(DATABASE_PATH):
        os.remove(DATABASE_PATH)

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # 3. Создаём таблицы
    cursor.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('manager', 'admin')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE generations (
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
        CREATE TABLE grout_colors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            hex_code TEXT NOT NULL
        );
        CREATE TABLE materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            filename TEXT NOT NULL,
            material_type TEXT NOT NULL CHECK(material_type IN ('standard', 'rigel', 'decorative_stone')),
            supplier TEXT NOT NULL CHECK(supplier IN ('redstone', 'redstone_premium', 'krasny_kamen')),
            UNIQUE(name, material_type, supplier)
        );
    """)

    # 4. Восстанавливаем admin из старой БД или создаём нового
    if admin:
        cursor.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (?, ?, ?, ?)",
            admin
        )
        print("Admin restored from old database.")
    else:
        pwd_hash = get_password_hash("admin")
        cursor.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", pwd_hash, "admin")
        )
        print("Default admin created (login: admin, password: admin).")

    # 5. Добавляем начальные цвета затирки
    default_colors = [
        ("Белый", "#FFFFFF"),
        ("Черный", "#000000"),
        ("Серый", "#808080"),
        ("Коричневый", "#8B4513"),
        ("Бежевый", "#F5F5DC")
    ]
    cursor.executemany(
        "INSERT INTO grout_colors (name, hex_code) VALUES (?, ?)",
        default_colors
    )
    print("Default grout colors added.")

    conn.commit()
    conn.close()
    print("Migration completed successfully.")


if __name__ == "__main__":
    migrate()