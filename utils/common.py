"""Общие утилиты, используемые в нескольких модулях."""
import os
from fastapi import Request

# Типы материалов без затирки (камень + сплошные текстуры)
STONE_TYPES: frozenset[str] = frozenset({"cobblestone", "rubble_stone", "derbent_stone", "flat_stone", "textured_stone", "solid"})


def resolve_public_base_url(request: Request) -> str:
    """Возвращает публичный base URL сервера (с учётом PUBLIC_BASE_URL из .env)."""
    configured = os.getenv("PUBLIC_BASE_URL")
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")
