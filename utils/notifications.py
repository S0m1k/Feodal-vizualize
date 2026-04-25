"""
Общий модуль уведомлений: email + in-app (таблица notifications).

Email получатели захардкожены. SMTP настраивается через .env:
  SMTP_HOST, SMTP_PORT (default 465), SMTP_USER, SMTP_PASS
"""
import os
import smtplib
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("notifications")

NOTIFY_EMAILS = ["spb@rstone.ru", "loginova@rstone.ru"]


def send_email_sync(subject: str, body: str) -> bool:
    """Синхронная отправка email. Вызывать через asyncio executor."""
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")

    if not all([smtp_host, smtp_user, smtp_pass]):
        logger.warning("SMTP не настроен. Задайте SMTP_HOST, SMTP_USER, SMTP_PASS в .env")
        return False

    msg = MIMEMultipart()
    msg["From"]    = smtp_user
    msg["To"]      = ", ".join(NOTIFY_EMAILS)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        ctx = ssl.create_default_context()
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, NOTIFY_EMAILS, msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls(context=ctx)
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, NOTIFY_EMAILS, msg.as_string())
        logger.info("Email отправлен: «%s» → %s", subject, NOTIFY_EMAILS)
        return True
    except Exception as e:
        logger.error("Ошибка отправки email: %s", e)
        return False


async def create_notification(db, type_: str, message: str) -> None:
    """Создаёт in-app уведомление в БД (aiosqlite соединение)."""
    try:
        await db.execute(
            "INSERT INTO notifications (type, message) VALUES (?, ?)",
            (type_, message),
        )
        await db.commit()
    except Exception as e:
        logger.error("Ошибка создания уведомления в БД: %s", e)
