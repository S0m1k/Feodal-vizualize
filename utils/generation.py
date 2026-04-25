import asyncio
import os
import logging
import time
import httpx

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


def build_prompt(category: str, material_type: str, grout_color_hex: str = None) -> str:
    """Единый промт для клиентского и внутреннего роутеров."""
    base = "Preserve house geometry, windows, doors, and environment EXACTLY."

    if material_type == "decorative_stone":
        prompt = (f"{base} Replace facade with the provided stone texture. "
                  f"The source image shows a small sample area of stone masonry. "
                  f"Tile this texture repeatedly across the walls so the stone blocks "
                  f"maintain a realistic architectural scale relative to the house. "
                  f"Each block should look like a medium-sized cladding panel. "
                  f"Ensure the pitted porous texture and subtle color shifts are preserved.")
    else:
        target = "house facade" if category == "facade" else "interior wall"

        if material_type == "standard":
            prompt = (f"{base} Replace {target} with the provided brick texture. "
                      f"Apply as a high-density brickwork pattern. "
                      f"The bricks must be small and frequent, matching a standard real-world brick scale. "
                      f"Sharp repetition, clear horizontal courses.")
        else:
            # Ригель: пропорция 1:8, высокая горизонтальная плотность
            prompt = (f"{base} Replace {target} with EXACTLY the provided texture. "
                      f"CRITICAL: Apply as Riegel-style bricks. Proportions: extremely long and thin. "
                      f"Height-to-width ratio 1:10. High horizontal density. "
                      f"Create a continuous linear aesthetic.")

    if grout_color_hex and material_type != "decorative_stone":
        prompt += f" Use grout color HEX {grout_color_hex} for mortar joints."

    prompt += " Hyper-realistic architectural rendering, 8k, photorealistic sunlight."
    return prompt


def build_belt_prompt(category: str) -> str:
    """Промт для сервиса 'Пояса': красная зона → декоративный горизонтальный пояс."""
    target = "house facade" if category == "facade" else "interior wall"
    return (
        f"Preserve all original {target} geometry, windows, doors, and environment EXACTLY. "
        f"The red-highlighted zones on the image indicate decorative belt courses. "
        f"In these red zones ONLY, apply the provided brick texture as a horizontal decorative "
        f"belt course with tight coursing pattern. "
        f"All areas outside the red zones must remain completely unchanged. "
        f"Hyper-realistic architectural rendering, 8k, photorealistic sunlight."
    )


def build_plinth_prompt() -> str:
    """Промт для сервиса 'Цоколь': красная зона внизу → облицовка цоколя."""
    return (
        f"Preserve all original facade geometry, windows, doors, and environment EXACTLY. "
        f"The red-highlighted zone at the bottom of the image indicates the plinth area. "
        f"In this red zone ONLY, apply the provided stone or brick texture as plinth cladding. "
        f"The plinth cladding must blend naturally with the wall material above. "
        f"All areas above the red zone must remain completely unchanged. "
        f"Hyper-realistic architectural rendering, 8k, photorealistic sunlight."
    )


async def generate_image(image_urls: list, prompt: str) -> dict:
    """
    Отправляет запрос к нейросети и возвращает {'output_url': url}.
    image_urls — список URL изображений (обычно [photo_url] или [photo_url, texture_url]).
    Использует асинхронный режим с опросом статуса.
    """
    api_key = os.getenv("GEN_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise Exception("Не задан API ключ (ожидается GEN_API_KEY или API_KEY)")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    payload = {
        "is_sync": False,
        "prompt": prompt,
        "image_urls": image_urls,
        "num_images": 1,
        "aspect_ratio": "16:9",
        "resolution": "1K",
        "output_format": "jpeg",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=5.0)) as client:
        # POST — не повторяем при ошибке чтения, чтобы не создать дубль задачи
        request_id = None
        try:
            logger.info(
                "GenAPI POST start endpoint=%s is_sync=%s num_images=%s num_image_urls=%s",
                NANO_BANANA_ENDPOINT,
                payload.get("is_sync"),
                payload.get("num_images"),
                len(image_urls),
            )
            t0 = time.monotonic()
            resp = await client.post(NANO_BANANA_ENDPOINT, json=payload, headers=headers)
            elapsed = round(time.monotonic() - t0, 2)
            if not resp.is_success:
                body_preview = resp.text[:500]
                logger.error("GenAPI POST failed status=%s body=%s", resp.status_code, body_preview)
                raise Exception(f"POST {resp.status_code}: {body_preview}")
            data = resp.json()
            request_id = data.get("request_id")
            logger.info("GenAPI POST success request_id=%s elapsed=%ss", request_id, elapsed)
            if not request_id:
                raise Exception(f"Не получен request_id: {data}")
        except httpx.ReadTimeout as e:
            logger.error("GenAPI POST read timeout (possible duplicate if server accepted request): %s", e)
            raise Exception(
                "Таймаут чтения ответа POST. Возможна постановка задачи в очередь без получения request_id; "
                "повтор автоматически не выполняется, чтобы избежать дублей."
            )
        except httpx.ConnectError as e:
            logger.error("GenAPI POST connection error: %s", e)
            raise Exception(f"Ошибка соединения при отправке запроса: {e}")

        if not request_id:
            raise Exception("Не удалось получить request_id")

        # Опрос статуса
        status_url = STATUS_ENDPOINT.format(request_id=request_id)
        poll_client = httpx.AsyncClient(timeout=30.0)
        async with poll_client:
            logger.info("GenAPI polling start request_id=%s", request_id)
            for attempt in range(90):  # до 3 минут
                await asyncio.sleep(2)
                try:
                    status_resp = await poll_client.get(status_url, headers=headers)
                    status_resp.raise_for_status()
                    status_data = status_resp.json()
                    status = str(status_data.get("status", "")).lower()
                    logger.info(
                        "GenAPI polling attempt=%d request_id=%s status=%s raw=%s",
                        attempt,
                        request_id,
                        status,
                        str(status_data)[:300],
                    )
                    if status in {"success", "done", "completed", "finished"}:
                        output_url = _extract_output_url(status_data)
                        if output_url:
                            logger.info(
                                "GenAPI polling success request_id=%s output_url=%s",
                                request_id,
                                output_url,
                            )
                            return {"output_url": output_url}
                        raise Exception(f"Результат не содержит URL: {status_data}")
                    if status in {"error", "failed", "failure"}:
                        raise Exception(f"Генерация не удалась: {status_data}")
                except httpx.TimeoutException:
                    logger.warning("GenAPI polling timeout attempt=%d request_id=%s", attempt, request_id)
                    continue
                except httpx.HTTPStatusError as e:
                    logger.warning(
                        "GenAPI polling http error attempt=%d request_id=%s status=%s",
                        attempt,
                        request_id,
                        e.response.status_code,
                    )
                    continue
                except httpx.RequestError as e:
                    logger.warning(
                        "GenAPI polling request error attempt=%d request_id=%s error=%s",
                        attempt,
                        request_id,
                        e,
                    )
                    continue

    raise Exception(f"Таймаут ожидания генерации (3 минуты), request_id={request_id}")
