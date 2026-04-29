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


from utils.common import STONE_TYPES as _STONE_TYPES


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

    if material_type == "rubble_stone":
        prompt = (f"{base} {zone_instr}Replace {target} with the provided texture. "
                  f"FORMAT: Wild rubble / irregular angular fieldstone. "
                  f"CRITICAL SCALE & DENSITY: The new stonework must consist of a high-density mosaic of small-to-medium stones (hand-sized relative to windows). "
                  f"Avoid oversized boulders or large stone slabs. Stones must be tightly packed with minimal visible gaps, creating a fine-grained architectural texture. "
                  f"TEXTURE & RELIEF: Each stone must have sharp, jagged edges and a matte, rugged surface. Maintain a natural, three-dimensional relief where individual stones protrude slightly. "
                  f"Strictly avoid smooth or glossy 'plastic-like' finishes. "
                  f"JOINTS: Incorporate deep, dark, recessed mortar joints to create realistic ambient occlusion and shadow depth between the small stones.")
    elif material_type == "cobblestone":
        prompt = (f"{base} {zone_instr}"
                  f"TASK: Replace {target} with raw, natural weathered cobblestone. "
                  f"CRITICAL ANTI-PLASTIC INSTRUCTIONS: "
                  f"Surface Texture: The stone surface must be matte, coarse, and granular with visible mineral pores and natural imperfections. "
                  f"Strictly avoid smooth, glossy, or plastic-like finishes. "
                  f"Scale & Density: Maintain a high-density mosaic of many small-to-medium stones (fist-sized relative to windows). "
                  f"Each stone must have a unique, organic shape — no identical or repetitive patterns. "
                  f"3D Relief: Incorporate deep, dark, irregular mortar joints (raked joints) between stones. "
                  f"This must create a strong ambient occlusion effect (soft shadows in the gaps), giving the wall real 3D tactile depth. "
                  f"Color Variance: Stones must have natural earthy color variations (mix of grays, tans, and browns) "
                  f"with subtle mineral staining to break the uniform sticker look. "
                  f"GEOMETRY: Keep the cladding flush with surrounding wall surfaces, but ensure each stone protrudes slightly for a realistic rugged texture.")
    elif material_type == "derbent_stone":
        prompt = (f"{base} {zone_instr}Replace {target} with a detailed pattern of very small, finely hand-tooled ashlar limestone masonry. "
                  f"CRITICAL DETAIL (Scale and Texture): The new stonework must consist of very small and numerous "
                  f"individual stones, mirroring the provided reference. "
                  f"These stones must have a matte, finely hand-tooled surface with a clean, cut look. "
                  f"The individual blocks must be flat and even — precise, hand-cut tiles rather than irregular rubble. "
                  f"CRITICAL DETAIL (Coursing and Joints): Lay blocks in very clean, tight, regular horizontal courses. "
                  f"Incorporate very deeply recessed and shadowed mortar joints (raked joints) between every stone block. "
                  f"These deep, dark shadows between flat clean blocks must create the primary relief — "
                  f"avoid any rugged or bumpy surface texture on the stone faces themselves. "
                  f"COLOR: Light beige/cream with subtle natural variation. "
                  f"Ensure all multi-level geometry and building details are preserved.")
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


def _derbent_zone_detail() -> str:
    """Детальное описание мелкомасштабного чистого тесаного камня для зональных промтов."""
    return (
        "Fill the entire red zone with a detailed pattern of very small, finely hand-tooled ashlar limestone masonry "
        "matching the provided reference. "
        "CRITICAL DETAIL (Scale and Texture): The stonework must consist of very small and numerous individual stones. "
        "Each stone must have a matte, finely hand-tooled surface with a clean, cut look — "
        "flat and even, like precise hand-cut tiles rather than irregular rubble. "
        "CRITICAL DETAIL (Coursing and Joints): Lay blocks in very clean, tight, regular horizontal courses with "
        "very deeply recessed and shadowed mortar joints (raked joints) between every stone. "
        "These deep dark shadows between flat clean blocks must create the primary relief — "
        "avoid any rugged or bumpy texture on the stone faces themselves. "
        "Use light beige/cream color with subtle natural variation. "
        "Blend seamlessly with surrounding surfaces at the zone edges. "
    )


def build_plinth_prompt(material_type: str = None) -> str:
    """Промт для подвкладки Цоколь."""
    base = (
        "Preserve all original house geometry, windows, and environment EXACTLY. "
        "The red-highlighted zone indicates the plinth. "
    )
    if material_type == "derbent_stone":
        fill = _derbent_zone_detail()
    else:
        fill = (
            "Replace the texture STRICTLY within the red zone with the provided texture. "
            "Ensure the new material (stone/brick) aligns with the perspective of the building. "
            "The cladding must look heavy and structural. "
        )
    return (
        base + fill +
        "Do NOT change anything outside the red zone. "
        "Hyper-realistic, 8k architectural visualization."
    )


def build_reika_prompt(orientation: str = "horizontal") -> str:
    """Промт для подвкладки Рейка."""
    orient_word = "Horizontal" if orientation == "horizontal" else "Vertical"
    return (
        "Preserve all original house geometry and lighting EXACTLY. "
        "The red-highlighted zone marks the area for decorative slats. "
        "Apply the provided reika (slatted) texture STRICTLY within this zone. "
        f"Orientation: {orient_word}. "
        "Slats must be perfectly straight, evenly spaced, and match the architectural scale of the building. "
        "Follow the orientation of the provided texture sample exactly. "
        "Ensure clean edges where the slats meet other materials. "
        "Do NOT change anything outside the red zone. "
        "8k, sharp focus on timber/metal texture."
    )


def build_belt_prompt(has_texture: bool = True, material_type: str = None) -> str:
    """Промт для подвкладки Пояса.
    Сценарий А (has_texture=True): заменить зону на выбранную текстуру.
    Сценарий Б (has_texture=False): перекомпоновать существующую кладку в солдатский ряд.
    """
    if has_texture:
        if material_type == "derbent_stone":
            fill = _derbent_zone_detail()
        else:
            fill = (
                "Replace the texture STRICTLY within the red zone with the provided texture. "
                "Match the orientation and scale of the sample. "
                "The belt must look integrated into the facade with realistic depth and shadows at the seams. "
            )
        return (
            "Preserve all original facade geometry. "
            "The red-highlighted horizontal zone is a decorative belt. " +
            fill +
            "Do NOT change anything outside the red zone. "
            "8k, photorealistic."
        )
    else:
        return (
            "ATTENTION: Change the EXISTING facade material orientation ONLY within the red-highlighted zone. "
            "TASK: Rearrange the current bricks into a Vertical Soldier Course. "
            "Bricks must stand upright on their ends, oriented vertically in a straight decorative row. "
            "CRITICAL ALIGNMENT: The decorative belt must be perfectly flush and level with the surrounding facade surfaces, "
            "staying on the exact same plane. "
            "Avoid any recession, offset, or 'sunken' effect. "
            "Ensure there are no deep shadow gaps at the top or bottom edges of the belt. "
            "MATERIAL MATCH: Use the EXACT same color, tone, and material as the surrounding wall. "
            "The only change is the orientation of the pattern. "
            "Do NOT change anything outside the red zone. "
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
