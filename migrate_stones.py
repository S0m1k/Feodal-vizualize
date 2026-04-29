"""
Одноразовая миграция: добавляет cobblestone / rubble_stone / derbent_stone
в CHECK-ограничение таблицы materials.

Запускать вручную (один раз):
    python migrate_stones.py

Скрипт делает резервную копию БД перед любыми изменениями.
"""

import sqlite3
import shutil
import os

DB_PATH = "data/app.db"
BACKUP_PATH = "data/app.db.bak_before_stones"

NEW_CHECK = (
    "'standard', 'rigel', 'decorative_stone', "
    "'cobblestone', 'rubble_stone', 'derbent_stone', 'reika'"
)


def needs_migration(conn: sqlite3.Connection) -> bool:
    cur = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='materials'"
    )
    row = cur.fetchone()
    return row is not None and "'cobblestone'" not in row[0]


def run():
    if not os.path.exists(DB_PATH):
        print(f"[migrate_stones] БД не найдена: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if not needs_migration(conn):
        print("[migrate_stones] Миграция уже применена — пропускаем.")
        conn.close()
        return

    # Резервная копия
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"[migrate_stones] Резервная копия: {BACKUP_PATH}")

    conn.executescript(f"""
        ALTER TABLE materials RENAME TO materials_old;

        CREATE TABLE materials (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            filename      TEXT    NOT NULL,
            material_type TEXT    NOT NULL CHECK(material_type IN ({NEW_CHECK})),
            supplier      TEXT    NOT NULL CHECK(supplier IN (
                'redstone', 'redstone_premium', 'krasny_kamen', 'reika'
            )),
            UNIQUE(name, material_type, supplier)
        );

        INSERT INTO materials SELECT * FROM materials_old;

        DROP TABLE materials_old;
    """)
    conn.commit()
    conn.close()
    print("[migrate_stones] Готово: новые типы камня добавлены в CHECK.")


if __name__ == "__main__":
    run()
