import requests
import asyncio
import os
import logging
import time
NANO_BANANA_ENDPOINT = "https://api.gen-api.ru/api/v1/networks/nano-banana-2"
STATUS_ENDPOINT = "https://api.gen-api.ru/api/v1/request/get/{request_id}"
logger = logging.getLogger("generation")


def _extract_output_url(status_data: dict) -> str | None:
    payload = status_data.get("output", status_data.get("result"))
    if payload is None:
        payload = status_data.get("response")

    if isinstance(payload, str):
        return payload
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("url") or first.get("image_url")
    if isinstance(payload, dict):
        url = payload.get("url") or payload.get("image_url")
        if url:
            return url
        urls = payload.get("urls")
        if isinstance(urls, list) and urls:
            return urls[0]
    return None

async def generate_image(image_url: str, texture_url: str, prompt: str) -> dict:
    """
    Отправляет запрос к нейросети и возвращает {'output_url': url}.
    Использует асинхронный режим с опросом статуса.
    """
    api_key = os.getenv("GEN_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise Exception("Не задан API ключ (ожидается GEN_API_KEY или API_KEY)")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "is_sync": False,
        "prompt": prompt,
        "image_urls": [image_url, texture_url],
        "num_images": 1,
        "aspect_ratio": "16:9",
        "resolution": "1K",
        "output_format": "jpeg"
    }

    # Для POST не делаем ретраи по ReadTimeout, чтобы не создать дубль задачи в очереди.
    request_id = None
    try:
        logger.info(
            "GenAPI POST start endpoint=%s is_sync=%s num_images=%s",
            NANO_BANANA_ENDPOINT,
            payload.get("is_sync"),
            payload.get("num_images"),
        )
        t0 = time.monotonic()
        resp = requests.post(NANO_BANANA_ENDPOINT, json=payload, headers=headers, timeout=(30, 300))
        elapsed = round(time.monotonic() - t0, 2)
        if not resp.ok:
            body_preview = resp.text[:500]
            logger.error("GenAPI POST failed status=%s body=%s", resp.status_code, body_preview)
            raise Exception(f"POST {resp.status_code}: {body_preview}")
        data = resp.json()
        request_id = data.get("request_id")
        logger.info("GenAPI POST success request_id=%s elapsed=%ss", request_id, elapsed)
        if not request_id:
            raise Exception(f"Не получен request_id: {data}")
    except requests.ReadTimeout as e:
        logger.error("GenAPI POST read timeout (possible duplicate if server accepted request): %s", e)
        raise Exception(
            "Таймаут чтения ответа POST. Возможна постановка задачи в очередь без получения request_id; "
            "повтор автоматически не выполняется, чтобы избежать дублей."
        )
    except requests.ConnectionError as e:
        logger.error("GenAPI POST connection error: %s", e)
        raise Exception(f"Ошибка соединения при отправке запроса: {e}")

    if not request_id:
        raise Exception("Не удалось получить request_id")

    # Опрос статуса
    status_url = STATUS_ENDPOINT.format(request_id=request_id)
    logger.info("GenAPI polling start request_id=%s", request_id)
    for _ in range(90):  # до 3 минут
        await asyncio.sleep(2)
        try:
            status_resp = requests.get(status_url, headers=headers, timeout=30)
            status_resp.raise_for_status()
            status_data = status_resp.json()
            status = str(status_data.get("status", "")).lower()
            logger.info("GenAPI polling request_id=%s status=%s", request_id, status)
            if status in {"success", "done", "completed", "finished"}:
                output_url = _extract_output_url(status_data)
                if output_url:
                    logger.info("GenAPI polling success request_id=%s output_url=%s", request_id, output_url)
                    return {"output_url": output_url}
                raise Exception(f"Результат не содержит URL: {status_data}")
            if status in {"error", "failed", "failure"}:
                raise Exception(f"Генерация не удалась: {status_data}")
        except requests.Timeout:
            logger.warning("GenAPI polling timeout request_id=%s", request_id)
            continue
        except requests.RequestException as e:
            logger.warning("GenAPI polling request exception request_id=%s error=%s", request_id, e)
            continue

    raise Exception(f"Таймаут ожидания генерации (3 минуты), request_id={request_id}")