import asyncio
import os
import logging
import time
import httpx

GPT_IMAGE_ENDPOINT = "https://api.gen-api.ru/api/v1/networks/gpt-image-2"
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
    base = (
        "STRICT ARCHITECTURAL PRESERVATION: Frozen geometry — Zero modifications to building silhouette. "
        "CRITICAL: Do NOT remove, move, or alter stairs, steps, balconies, or window frames. "
        "Preserve house geometry, windows, doors, and lighting EXACTLY."
    )
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
                  f"CRITICAL 3D VOLUME (most important rule): Every single stone must read as a fully three-dimensional rounded boulder. "
                  f"Each stone is a convex sphere or ovoid — the center of its visible face protrudes outward, and the edges curve away from the viewer into the dark gaps. "
                  f"Strong directional highlight on the topmost/facing surface of each stone; deep cast shadow on the lower/side edges. "
                  f"The overall wall surface must have dramatic high-relief depth — NOT a flat appliqué of stone shapes. "
                  f"GAPS: Gaps between stones must be deep, dark, and recessed — strong ambient occlusion creating the impression of real physical depth between rounded boulders. "
                  f"SURFACE TEXTURE: Matte, coarse, granular mineral surface with visible pores and natural imperfections. Strictly avoid smooth, glossy, or plastic-like finishes. "
                  f"SCALE & DENSITY: High-density mosaic of many small-to-medium stones (fist-sized relative to windows). Each stone has a unique organic shape — no identical or repetitive patterns. "
                  f"COLOR VARIANCE: Natural earthy variations (grays, tans, browns) with subtle mineral staining. "
                  f"FORBIDDEN: Flat stone faces. Silhouette-like 2D stone shapes. Uniform, identical stones.")
    elif material_type in ("textured_stone", "derbent_stone"):
        prompt = (f"{base} {zone_instr}Replace {target} with the provided textured square stone cladding. "
                  f"BLOCK SCALE (CRITICAL): Blocks must be SMALL — each block should be roughly fist-sized relative to windows and doors. "
                  f"High density of blocks across the entire surface. Do NOT use large slabs or oversized stones. "
                  f"BLOCK GEOMETRY: Regular square or rectangular blocks with clearly defined, sharp block boundaries. Consistent coursing and alignment. "
                  f"SURFACE TEXTURE: Each stone face has a pronounced rough, split-face texture — visible peaks, valleys, and natural mineral roughness. NOT flat, NOT smooth. "
                  f"3D RELIEF: Each block face protrudes from the wall plane with moderate relief (1-3 cm). "
                  f"Strong self-shadowing within each block's textured face; dark recessed shadow at every joint line. "
                  f"JOINTS: Deep, dark recessed mortar lines between all blocks — consistent width and depth. "
                  f"FORBIDDEN: Large blocks or slabs. Smooth or flat block faces. Irregular stone shapes. Plastic or glossy surface.")
    elif material_type == "flat_stone":
        prompt = (f"{base} {zone_instr}Replace {target} with the provided flat square stone cladding. "
                  f"BLOCK SCALE (CRITICAL): The stone blocks must be MUCH SMALLER — roughly fist-sized relative to the windows. "
                  f"Create a high-density grid of numerous small rectangular stones. Do NOT use large slabs or oversized panels. "
                  f"SURFACE & GEOMETRY: Completely flat and smooth surface — no chipping, no roughness, no protruding bumps. "
                  f"Planar and calibrated precision-cut architectural stone. Sharp, precise clean-cut edges with straight horizontal courses in a perfect grid alignment. "
                  f"JOINTS: Very thin, sharp, and uniform recessed mortar lines. The grid must be clean and regular. "
                  f"SHADOWS: Subtle occlusion only at the thin joint lines to define the small block boundaries. "
                  f"VISUAL OUTPUT: Render ONLY the house with the applied texture. Do NOT display the source texture sample or any UI elements on the final image.")
    elif material_type == "standard":
        prompt = (f"{base} {zone_instr}Replace {target} with the provided brick texture. "
                  f"Apply as high-density brickwork. Bricks must be small and frequent, "
                  f"matching realistic standard brick dimensions. Tight alignment.")
    else:
        # Ригель: пропорция 1:12, экстремально тонкие кирпичи
        prompt = (f"{base} {zone_instr}Replace {target} with EXACTLY the provided texture. "
                  f"Format: Riegel brick (extreme ultra-long, razor-thin). Ratio 1:12. "
                  f"Each brick must be exceptionally thin and needle-like. "
                  f"Apply with maximum horizontal density and razor-sharp, razor-thin linear courses.")

    if grout_color_hex and material_type not in _STONE_TYPES:
        prompt += f" Mortar joints color: HEX {grout_color_hex}."

    prompt += " Hyper-realistic, 8k architectural visualization, sharp focus on masonry."
    return prompt


def _derbent_zone_detail() -> str:
    """Детальное описание Melen Brutal 3D Rockface — для зональных промтов (plinth, belt)."""
    return (
        "GEOMETRY LOCK (CRITICAL): Apply texture STRICTLY within the red zone boundaries ONLY. "
        "Do NOT bleed into stairs, ground level, or any adjacent building structures. "
        "ZERO-BLEED POLICY: Stone relief must NOT protrude upwards past the red boundary line. "
        "FLUSH TOP EDGE: While the stone face is rugged, the top-most row must end in a perfectly straight line following the mask. "
        "Maintain pixel-perfect precision at zone edges. Zero structural distortion. "
        "Fill the entire red zone with ultra-rugged, heavy-relief Melen stone masonry matching the provided reference. "
        "GEOMETRY DESTRUCTION (The 'No-Flat' Rule): Extreme 3D Protrusion — Each individual stone must physically protrude from the wall at various depths (3-6cm). Strictly forbid flat surfaces or smooth planes. "
        "Jagged Rock-Face — Use an extreme rock-face texture with sharp, jagged protrusions and deep, dark natural crevices. The surface must look like raw, unpolished mountain rock. "
        "Broken Edges — The edges of each stone must be irregular and chipped, not straight lines. "
        "LIGHTING & SHADOWS: High-Contrast Relief — The sunlight must create harsh, deep shadows within the stone's own texture. "
        "Ambient Occlusion — Every stone block must cast a physical shadow onto the stones below it. "
        "JOINTS: Seamless Dry-Stack — Stones are tightly packed with zero grout gaps. The only 'lines' allowed are the deep, irregular shadow-cracks between the rugged stone faces. Strictly avoid any regular grid or mortar lines. "
        "FORBIDDEN: NO bricks, NO tiles, NO flat masonry, NO smooth surfaces. "
        "SCALE: Small-to-medium stones relative to windows for realism. "
        "Blend seamlessly with surrounding surfaces at the zone edges. "
    )


def build_plinth_prompt(material_type: str = None) -> str:
    """Промт для подвкладки Цоколь."""
    base = (
        "STRICT BOUNDARY CONTROL: The new texture must stop EXACTLY at the top edge of the red-highlighted zone. "
        "CRITICAL: Do NOT extend the masonry height above the red line. "
        "NO LEDGE / NO CAP: The top of the plinth must be perfectly flush with the wall above it. "
        "Strictly forbid any decorative trim, borders, ledges, or protruding caps at the upper boundary. "
        "PIXEL-PERFECT ALIGNMENT: Match the perspective and height of the red zone with 100% precision. "
        "Keep all stairs, railings, and architectural elements outside the zone frozen and unchanged. "
    )
    if material_type == "derbent_stone":
        fill = _derbent_zone_detail()
    else:
        fill = (
            "Replace the texture STRICTLY within the red zone with the provided texture. "
            "Ensure the new material (stone/brick) aligns with the perspective of the building. "
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
        "STRICT ARCHITECTURAL PRESERVATION: Frozen geometry. "
        "CRITICAL: Do NOT modify window positions, doors, or structural elements. "
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


def build_belt_prompt(has_texture: bool = True, material_type: str = None,
                      belt_mode: str = "soldier") -> str:
    """Промт для подвкладки Пояса.
    Сценарий А (has_texture=True): заменить зону на выбранную текстуру.
    Сценарий Б (has_texture=False, belt_mode='soldier'): солдатский ряд.
    Сценарий В (has_texture=False, belt_mode='chess'): шахматная вертикальная кладка.
    """
    base_constraint = (
        "STRICT ARCHITECTURAL PRESERVATION: Frozen geometry — Zero modifications to building silhouette or window positions. "
        "CRITICAL: Do NOT shift, remove, or alter any window frames or door elements. "
        "BELT WIDTH LOCK: The decorative belt must maintain its exact vertical height — "
        "Do NOT expand, compress, or distort the height of the belt zone. "
    )

    if has_texture:
        if material_type in ("textured_stone", "derbent_stone"):
            fill = _derbent_zone_detail()
        else:
            fill = (
                "Replace the texture STRICTLY within the red zone with the provided texture. "
                "Match the orientation and scale of the sample. "
                "The belt must look integrated into the facade with realistic depth and shadows at the seams. "
            )
        return (
            base_constraint +
            "Preserve all original facade geometry. "
            "The red-highlighted horizontal zone is a decorative belt. " +
            fill +
            "Do NOT change anything outside the red zone. "
            "8k, photorealistic."
        )
    elif belt_mode == "chess":
        return (
            base_constraint +
            "ATTENTION: Change the EXISTING facade material orientation ONLY within the red-highlighted zone. "
            "TASK: Rearrange the current bricks into a Vertical Staggered (Running Bond) pattern. "
            "Bricks must stand upright on their ends, oriented vertically. "
            "STAGGER RULE (CRITICAL): Adjacent vertical columns must be offset by exactly half a brick height — "
            "like a standard horizontal running bond pattern, but rotated 90 degrees. "
            "The result is a chess-like diagonal rhythm: one column starts at the top of the zone, "
            "the next column starts half a brick lower, alternating across the entire belt. "
            "CRITICAL ALIGNMENT: The belt must be perfectly flush with the surrounding facade surfaces, "
            "staying on the exact same plane. No recession, no 'sunken' effect. "
            "No deep shadow gaps at the top or bottom edges. "
            "MATERIAL MATCH: Use the EXACT same color, tone, and material as the surrounding wall. "
            "The only change is the pattern and orientation. "
            "Do NOT change anything outside the red zone. "
            "Hyper-realistic architectural rendering, 8k, photorealistic lighting."
        )
    else:
        return (
            base_constraint +
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
        "prompt": prompt,
        "image_urls": image_urls,
        "quality": "medium",
        "image_size": "1024x1024",
        "num_images": 1,
        "output_format": "png",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=5.0)) as client:
        # POST — не повторяем при ошибке чтения, чтобы не создать дубль задачи
        request_id = None
        try:
            logger.info(
                "GenAPI POST start endpoint=%s quality=%s num_images=%s num_image_urls=%s",
                GPT_IMAGE_ENDPOINT,
                payload.get("quality"),
                payload.get("num_images"),
                len(image_urls),
            )
            t0 = time.monotonic()
            resp = await client.post(GPT_IMAGE_ENDPOINT, json=payload, headers=headers)
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
            for attempt in range(150):  # до 5 минут
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
