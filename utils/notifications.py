"""
Общий модуль уведомлений: email + in-app (таблица notifications).

Email получатели захардкожены. SMTP настраивается через .env:
  SMTP_HOST, SMTP_PORT (default 465), SMTP_USER, SMTP_PASS
"""
import os
import socket
import smtplib
import ssl
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("notifications")

NOTIFY_EMAILS = ["spb@rstone.ru", "loginova@rstone.ru"]

_SMTP_TIMEOUT = 15  # секунд на попытку подключения


def _ipv6_first_socket(host: str, port: int, timeout: float) -> socket.socket:
    """TCP-сокет с предпочтением IPv6 (IPv4 может быть заблокирован провайдером)."""
    addrs = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    addrs.sort(key=lambda x: 0 if x[0] == socket.AF_INET6 else 1)
    last_exc: Exception = OSError(f"Не удалось подключиться к {host}:{port}")
    for af, socktype, proto, _, sockaddr in addrs:
        sock = socket.socket(af, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(sockaddr)
            return sock
        except OSError as e:
            last_exc = e
            sock.close()
    raise last_exc


class _SMTP_SSL_IPv6(smtplib.SMTP_SSL):
    """SMTP_SSL, предпочитающий IPv6 при подключении."""
    def _get_socket(self, host, port, timeout):
        t = _SMTP_TIMEOUT if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
        raw = _ipv6_first_socket(host, port, t)
        return self.context.wrap_socket(raw, server_hostname=self._host)


class _SMTP_IPv6(smtplib.SMTP):
    """SMTP (STARTTLS), предпочитающий IPv6 при подключении."""
    def _get_socket(self, host, port, timeout):
        t = _SMTP_TIMEOUT if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
        return _ipv6_first_socket(host, port, t)


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
            with _SMTP_SSL_IPv6(smtp_host, smtp_port, context=ctx) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, NOTIFY_EMAILS, msg.as_string())
        else:
            with _SMTP_IPv6(smtp_host, smtp_port) as server:
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
