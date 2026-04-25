"""
Фоновая задача: проверка баланса GenAPI и уведомление.

Настройки в .env:
  GEN_API_KEY             — ключ GenAPI
  BALANCE_THRESHOLD       — порог в рублях (по умолчанию 300)
  BALANCE_CHECK_INTERVAL  — секунды между проверками (по умолчанию 3600)
  SMTP_HOST/PORT/USER/PASS — настройки email (см. utils/notifications.py)
"""

import asyncio
import logging
import os

import httpx

from utils.notifications import send_email_sync, create_notification
from database import get_db

logger = logging.getLogger("balance_checker")

GENAPI_USER_ENDPOINT = "https://api.gen-api.ru/api/v1/user"
_NOTIFIED_KEY = "balance_low_notified"


async def check_balance_once(redis_client) -> None:
    api_key   = os.getenv("GEN_API_KEY") or os.getenv("API_KEY")
    threshold = float(os.getenv("BALANCE_THRESHOLD", "300"))

    if not api_key:
        logger.warning("GEN_API_KEY не задан, проверка баланса пропущена")
        return

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                GENAPI_USER_ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data    = resp.json()
            balance = float(data.get("balance", 0))
            logger.info("Баланс GenAPI: %.2f ₽ (порог %.0f ₽)", balance, threshold)
    except Exception as e:
        logger.error("Ошибка получения баланса GenAPI: %s", e)
        return

    if balance < threshold:
        already_notified = redis_client.get(_NOTIFIED_KEY)
        if not already_notified:
            subject = f"⚠️ Низкий баланс GenAPI: {balance:.2f} ₽"
            body = (
                f"Баланс аккаунта GenAPI составляет {balance:.2f} ₽, "
                f"что ниже порога {threshold:.0f} ₽.\n\n"
                f"Пополните баланс на https://gen-api.ru чтобы избежать остановки генераций.\n\n"
                f"— Rstone автомониторинг"
            )
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, send_email_sync, subject, body)

            # In-app уведомление
            db = await get_db()
            try:
                await create_notification(db, "low_balance",
                    f"Низкий баланс GenAPI: {balance:.2f} ₽ (порог {threshold:.0f} ₽)")
            finally:
                await db.close()

            redis_client.setex(_NOTIFIED_KEY, 43200, "1")  # 12 часов
    else:
        redis_client.delete(_NOTIFIED_KEY)


async def balance_checker_loop(redis_client) -> None:
    interval = int(os.getenv("BALANCE_CHECK_INTERVAL", "3600"))
    logger.info("Balance checker запущен (интервал %ds, порог %s ₽)",
                interval, os.getenv("BALANCE_THRESHOLD", "300"))
    await asyncio.sleep(60)
    while True:
        await check_balance_once(redis_client)
        await asyncio.sleep(interval)
