import aiosqlite
import sqlite3
import os
from rauth import get_password_hash

DATABASE_PATH = "data/app.db"

async def get_db():
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
            material_type TEXT NOT NULL CHECK(material_type IN ('standard', 'rigel', 'cobblestone', 'rubble_stone', 'derbent_stone', 'reika')),
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

    # Очистка: удалить materials_old если осталась от прерванной миграции
    conn.execute("DROP TABLE IF EXISTS materials_old")
    conn.commit()

    # Миграция: расширить CHECK-ограничения materials, если 'reika' ещё не включён
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='materials'")
    row = cur.fetchone()
    if row and "CHECK" in row[0] and "'reika'" not in row[0]:
        conn.execute("ALTER TABLE materials RENAME TO materials_old")
        conn.execute("""
            CREATE TABLE materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filename TEXT NOT NULL,
                material_type TEXT NOT NULL CHECK(material_type IN ('standard', 'rigel', 'cobblestone', 'rubble_stone', 'derbent_stone', 'reika')),
                supplier TEXT NOT NULL CHECK(supplier IN ('redstone', 'redstone_premium', 'krasny_kamen', 'reika')),
                UNIQUE(name, material_type, supplier)
            )
        """)
        conn.execute("INSERT INTO materials SELECT * FROM materials_old")
        conn.execute("DROP TABLE materials_old")
        conn.commit()

    # Миграция: добавить cobblestone/rubble_stone/derbent_stone если их нет в CHECK
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='materials'")
    row = cur.fetchone()
    if row and "CHECK" in row[0] and "'cobblestone'" not in row[0]:
        conn.execute("ALTER TABLE materials RENAME TO materials_old")
        conn.execute("""
            CREATE TABLE materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filename TEXT NOT NULL,
                material_type TEXT NOT NULL CHECK(material_type IN ('standard', 'rigel', 'cobblestone', 'rubble_stone', 'derbent_stone', 'reika')),
                supplier TEXT NOT NULL CHECK(supplier IN ('redstone', 'redstone_premium', 'krasny_kamen', 'reika')),
                UNIQUE(name, material_type, supplier)
            )
        """)
        conn.execute("INSERT INTO materials SELECT * FROM materials_old")
        conn.execute("DROP TABLE materials_old")
        conn.commit()

    # Миграция: убрать decorative_stone из CHECK и удалить такие записи
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='materials'")
    row = cur.fetchone()
    if row and "CHECK" in row[0] and "'decorative_stone'" in row[0]:
        conn.execute("DELETE FROM materials WHERE material_type = 'decorative_stone'")
        conn.execute("ALTER TABLE materials RENAME TO materials_old")
        conn.execute("""
            CREATE TABLE materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filename TEXT NOT NULL,
                material_type TEXT NOT NULL CHECK(material_type IN ('standard', 'rigel', 'cobblestone', 'rubble_stone', 'derbent_stone', 'reika')),
                supplier TEXT NOT NULL CHECK(supplier IN ('redstone', 'redstone_premium', 'krasny_kamen', 'reika')),
                UNIQUE(name, material_type, supplier)
            )
        """)
        conn.execute("INSERT INTO materials SELECT * FROM materials_old")
        conn.execute("DROP TABLE materials_old")
        conn.commit()

    # Миграция: добавить flat_stone и textured_stone, перевести derbent_stone → textured_stone
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='materials'")
    row = cur.fetchone()
    if row and "CHECK" in row[0] and "'flat_stone'" not in row[0]:
        conn.execute("ALTER TABLE materials RENAME TO materials_old")
        conn.execute("""
            CREATE TABLE materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filename TEXT NOT NULL,
                material_type TEXT NOT NULL CHECK(material_type IN ('standard', 'rigel', 'cobblestone', 'rubble_stone', 'derbent_stone', 'flat_stone', 'textured_stone', 'reika')),
                supplier TEXT NOT NULL CHECK(supplier IN ('redstone', 'redstone_premium', 'krasny_kamen', 'reika')),
                UNIQUE(name, material_type, supplier)
            )
        """)
        conn.execute("INSERT INTO materials SELECT * FROM materials_old")
        conn.execute("DROP TABLE materials_old")
        conn.commit()

    # Отдельная миграция: derbent_stone → textured_stone (идемпотентна, можно запускать повторно)
    conn.execute("UPDATE materials SET material_type = 'textured_stone' WHERE material_type = 'derbent_stone'")
    conn.commit()

    # Миграция: добавить riegel_mixed
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='materials'")
    row = cur.fetchone()
    if row and "CHECK" in row[0] and "'riegel_mixed'" not in row[0]:
        conn.execute("ALTER TABLE materials RENAME TO materials_old")
        conn.execute("""
            CREATE TABLE materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filename TEXT NOT NULL,
                material_type TEXT NOT NULL CHECK(material_type IN ('standard', 'rigel', 'riegel_mixed', 'cobblestone', 'rubble_stone', 'derbent_stone', 'flat_stone', 'textured_stone', 'reika')),
                supplier TEXT NOT NULL CHECK(supplier IN ('redstone', 'redstone_premium', 'krasny_kamen', 'reika')),
                UNIQUE(name, material_type, supplier)
            )
        """)
        conn.execute("INSERT INTO materials SELECT * FROM materials_old")
        conn.execute("DROP TABLE materials_old")
        conn.commit()

    # Миграция: убрать CHECK constraint с materials.material_type для поддержки кастомных типов
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='materials'")
    row = cur.fetchone()
    if row and "CHECK" in row[0]:
        conn.execute("ALTER TABLE materials RENAME TO materials_old")
        conn.execute("""
            CREATE TABLE materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filename TEXT NOT NULL,
                material_type TEXT NOT NULL,
                supplier TEXT NOT NULL,
                UNIQUE(name, material_type, supplier)
            )
        """)
        conn.execute("INSERT INTO materials SELECT * FROM materials_old")
        conn.execute("DROP TABLE materials_old")
        conn.commit()

    # Таблица кастомных типов материалов
    conn.execute("""
        CREATE TABLE IF NOT EXISTS custom_material_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            default_model TEXT DEFAULT 'gpt-image-2',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL CHECK(category IN ('fix', 'style')),
            label TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        )
    """)
    # Переопределения системного промта/модели для ВСТРОЕННЫХ типов материалов
    conn.execute("""
        CREATE TABLE IF NOT EXISTS material_type_overrides (
            slug TEXT PRIMARY KEY,
            system_prompt TEXT,
            default_model TEXT
        )
    """)
    conn.commit()

    # Миграция: добавить колонку model_used в generations
    cur = conn.execute("PRAGMA table_info(generations)")
    columns = [row[1] for row in cur.fetchall()]
    if "model_used" not in columns:
        conn.execute("ALTER TABLE generations ADD COLUMN model_used TEXT")
        conn.commit()

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
        ]
        for name, filename, mtype, supplier in materials:
            conn.execute("INSERT INTO materials (name, filename, material_type, supplier) VALUES (?, ?, ?, ?)",
                         (name, filename, mtype, supplier))

    # Создаём администратора
    cur = conn.execute("SELECT id FROM users WHERE username = ?", ("admin",))
    if not cur.fetchone():
        import os as _os
        admin_password = _os.getenv("ADMIN_PASSWORD")
        if not admin_password:
            raise RuntimeError(
                "ADMIN_PASSWORD не задан! Добавьте в .env строку вида: "
                "ADMIN_PASSWORD=<сложный пароль минимум 12 символов>"
            )
        pwd_hash = get_password_hash(admin_password)
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                     ("admin", pwd_hash, "admin"))
        conn.commit()
    conn.close()