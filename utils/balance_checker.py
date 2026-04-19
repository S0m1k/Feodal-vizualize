"""
Фоновая задача: проверка баланса GenAPI и email-уведомление.

Настройки в .env:
  GEN_API_KEY        — ключ GenAPI (уже используется)
  SMTP_HOST          — smtp.yandex.ru / smtp.mail.ru / smtp.gmail.com
  SMTP_PORT          — 465 (SSL) или 587 (STARTTLS)
  SMTP_USER          — логин ящика отправителя (например noreply@rstone.ru)
  SMTP_PASS          — пароль приложения
  BALANCE_THRESHOLD  — порог в рублях (по умолчанию 300)
  BALANCE_CHECK_INTERVAL — секунды между проверками (по умолчанию 3600)

Получатели захардкожены: spb@rstone.ru, loginova@rstone.ru
"""

import asyncio
import logging
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import httpx

logger = logging.getLogger("balance_checker")

GENAPI_USER_ENDPOINT = "https://api.gen-api.ru/api/v1/user"
_NOTIFIED_KEY = "balance_low_notified"   # Redis key


NOTIFY_EMAILS = ["spb@rstone.ru", "loginova@rstone.ru"]


def _send_email(balance: float, threshold: float) -> None:
    """Синхронная отправка письма (вызывается в executor)."""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")

    if not all([smtp_host, smtp_user, smtp_pass]):
        logger.warning("Email не настроен: задайте SMTP_HOST, SMTP_USER, SMTP_PASS в .env")
        return

    subject = f"⚠️ Низкий баланс GenAPI: {balance:.2f} ₽"
    body = (
        f"Баланс аккаунта GenAPI составляет {balance:.2f} ₽, "
        f"что ниже порога {threshold:.0f} ₽.\n\n"
        f"Пополните баланс на https://gen-api.ru чтобы избежать остановки генераций.\n\n"
        f"— Rstone автомониторинг"
    )

    msg = MIMEMultipart()
    msg["From"]    = smtp_user
    msg["To"]      = ", ".join(NOTIFY_EMAILS)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        if smtp_port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, NOTIFY_EMAILS, msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls(context=ssl.create_default_context())
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, NOTIFY_EMAILS, msg.as_string())
        logger.info("Email-уведомление отправлено на %s", NOTIFY_EMAILS)
    except Exception as e:
        logger.error("Ошибка отправки email: %s", e)


async def check_balance_once(redis_client) -> None:
    """Одна проверка баланса. redis_client — синхронный Redis из main.py."""
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
        # Отправляем не чаще 1 раза в 12 часов
        already_notified = redis_client.get(_NOTIFIED_KEY)
        if not already_notified:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _send_email, balance, threshold)
            redis_client.setex(_NOTIFIED_KEY, 43200, "1")  # 12 часов
    else:
        # Баланс восстановлен — сбрасываем флаг
        redis_client.delete(_NOTIFIED_KEY)


async def balance_checker_loop(redis_client) -> None:
    """Бесконечный цикл проверки баланса. Запускается при старте FastAPI."""
    interval = int(os.getenv("BALANCE_CHECK_INTERVAL", "3600"))  # раз в час
    logger.info("Balance checker запущен (интервал %ds, порог %s ₽)",
                interval, os.getenv("BALANCE_THRESHOLD", "300"))
    # Первая проверка через минуту после старта
    await asyncio.sleep(60)
    while True:
        await check_balance_once(redis_client)
        await asyncio.sleep(interval)
