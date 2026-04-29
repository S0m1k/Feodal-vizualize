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


_STONE_TYPES = {"decorative_stone", "cobblestone", "rubble_stone", "derbent_stone"}


def build_prompt(category: str, material_type: str,
                 grout_color_hex: str = None, use_zone: bool = False) -> str:
    """Промт для Облицовки (клиент + внутренний)."""
    base = "Preserve house geometry, windows, doors, and lighting EXACTLY."
    target = "house facade" if category == "facade" else "interior wall"
    zone_instr = (
        "The red-highlighted zone marks the area to be retextured. "
        "Apply the new texture STRICTLY within the red zone ONLY. "
        "Do NOT change anything outside the red zone. "
    ) if use_zone else ""

    if material_type == "decorative_stone":
        prompt = (f"{base} {zone_instr}Replace {target} with the provided stone texture. "
                  f"Format: Ledgestone / Stacked stone. "
                  f"The elements must be significantly smaller and thinner than original wall panels. "
                  f"Create a dense, intricate pattern of narrow horizontal stone strips. "
                  f"Tile the texture with high frequency to ensure a realistic architectural scale. "
                  f"Preserve the rough, natural rock relief and color depth.")
    elif material_type == "rubble_stone":
        prompt = (f"{base} {zone_instr}Replace {target} with the provided texture. "
                  f"Format: Wild rubble / irregular angular fieldstone. "
                  f"Stones must be tightly packed, varying in size and shape with sharp, jagged edges. "
                  f"Maintain a natural, rugged relief and organic placement.")
    elif material_type == "cobblestone":
        prompt = (f"{base} {zone_instr}Replace {target} with the provided texture. "
                  f"Format: Rounded river stones / smooth cobblestones. "
                  f"Stones should have soft, weathered edges and organic, non-linear placement. "
                  f"Emphasize the tactile, smooth surface and depth of mortar joints.")
    elif material_type == "derbent_stone":
        prompt = (f"{base} {zone_instr}Replace {target} with the provided texture. "
                  f"Format: Clean-cut rectangular ashlar masonry. "
                  f"Stones are uniform in height or arranged in precise horizontal courses "
                  f"with sharp right-angle corners. Surface is flat and refined. "
                  f"Maintain clean, tight joints.")
    elif material_type == "standard":
        prompt = (f"{base} {zone_instr}Replace {target} with the provided brick texture. "
                  f"Apply as high-density brickwork. Bricks must be small and frequent, "
                  f"matching realistic standard brick dimensions. Tight alignment.")
    else:
        # Ригель: пропорция 1:10, высокая горизонтальная плотность
        prompt = (f"{base} {zone_instr}Replace {target} with EXACTLY the provided texture. "
                  f"Format: Riegel brick (ultra-long, ultra-thin). Ratio 1:10. "
                  f"Apply with maximum horizontal density and sharp linear courses.")

    if grout_color_hex and material_type not in _STONE_TYPES:
        prompt += f" Mortar joints color: HEX {grout_color_hex}."

    prompt += " Hyper-realistic, 8k architectural visualization, sharp focus on masonry."
    return prompt


def build_accent_prompt() -> str:
    """Универсальный промт для Акцентов (бывш. Пояса / Цоколь / Рейка).
    Текстура уже повёрнута на клиенте — нейросеть копирует ориентацию из референса.
    """
    return (
        "Preserve all original house geometry, windows, doors, and surrounding environment EXACTLY. "
        "The red-highlighted zone marks the area for the new architectural element. "
        "STRICT INSTRUCTION: Apply the provided texture EXACTLY as shown in the reference image, "
        "matching its orientation, scale, and direction (horizontal/vertical) precisely within the red zone. "
        "Do NOT change the texture angle. "
        "The element must blend seamlessly with the surrounding environment at its edges. "
        "All areas outside the red zone must remain completely unchanged. "
        "Hyper-realistic architectural rendering, 8k, photorealistic lighting."
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
