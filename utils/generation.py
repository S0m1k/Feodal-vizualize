import asyncio
import os
import logging
import time
import httpx

NANO_BANANA_ENDPOINT = "https://api.gen-api.ru/api/v1/networks/nano-banana-2"
GPT_IMAGE_ENDPOINT = "https://api.gen-api.ru/api/v1/networks/gpt-image-2"
STATUS_ENDPOINT = "https://api.gen-api.ru/api/v1/request/get/{request_id}"
logger = logging.getLogger("generation")

# Типы материалов, для которых используется Nano Banana (чёткие кирпичные паттерны)
_NANO_BANANA_TYPES = {"standard", "rigel"}


def _select_endpoint(material_type: str | None) -> str:
    """Выбирает API эндпоинт в зависимости от типа материала."""
    if material_type in _NANO_BANANA_TYPES:
        return NANO_BANANA_ENDPOINT
    return GPT_IMAGE_ENDPOINT


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
                  f"FORMAT: Irregular rubble / angular fieldstone masonry. "
                  f"EDGES (CRITICAL — most important rule): Every single stone MUST have razor-sharp, abrupt, angular edges. "
                  f"Stone boundaries are hard, decisive lines — NOT rounded, NOT soft, NOT blurred. "
                  f"Each stone face meets its neighbour at a crisp, clearly defined break with no transitional blending. "
                  f"Reject any rounded, smoothed, or river-worn look — this is freshly split, rough-fractured stone. "
                  f"SCALE & DENSITY: High-density mosaic of small-to-medium irregularly shaped stones (hand-sized relative to windows). "
                  f"Avoid large slabs or boulders. Stones are tightly packed. "
                  f"SURFACE TEXTURE: Matte, coarse, split-face mineral surface with visible grain. Strictly no smooth or glossy finishes. "
                  f"RELIEF: Each stone protrudes slightly in 3D — sharp facets catch light along the top edge, cast hard shadow along the bottom. "
                  f"JOINTS: Deep, dark, recessed mortar lines with sharp-edged borders — strong ambient occlusion between stones.")
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
    elif material_type == "solid":
        prompt = (f"{base} {zone_instr}Replace {target} with the provided solid surface texture. "
                  f"CONTINUOUS SURFACE (CRITICAL): Render as a single uninterrupted plane — "
                  f"NO joints, NO mortar lines, NO seams, NO grout, NO transitions, NO blocks, NO bricks. "
                  f"The colour and micro-texture of the source sample must wrap the wall as ONE continuous coat. "
                  f"SUBTLE NATURAL VARIATION: Allow gentle, organic colour and tonal variation as in the source — "
                  f"but never introduce repeating tiles or grid patterns. "
                  f"FINISH: Match the matte/glossy character of the source sample exactly. "
                  f"FORBIDDEN: any kind of joint line, panel edge, or visible repetition.")
    elif material_type == "standard":
        prompt = (f"{base} {zone_instr}Replace {target} with the provided brick texture. "
                  f"Apply as high-density brickwork. Bricks must be small and frequent, "
                  f"matching realistic standard brick dimensions. Tight alignment.")
    elif material_type == "riegel_mixed":
        prompt = (f"{base} {zone_instr}Replace {target} with high-end multi-format Riegel masonry. "
                  f"EXTREME PROPORTIONS (1:15): The primary bricks must be needle-thin and exceptionally long. Ratio 1:15 is the target for the main units. "
                  f"3D LENGTH DYNAMICS: Blocks must alternate randomly in length. Mix extra-long 'needle' bricks with short 'header' blocks in every direction. No two adjacent bricks should have the same length. "
                  f"NON-LINEAR HORIZONTAL COURSES: Slightly vary the height of blocks (by 10-15%) to break the perfect horizontal line. The masonry should look like a complex mosaic, not a standard grid. "
                  f"DEEP SHADOWS: High-relief recessed joints. The 'interruption' of the lines must be clearly visible through shadows between blocks of different lengths and heights. "
                  f"100% MASK LOCK: Stop exactly at the red line. Cut or crop any block that exceeds the boundary. "
                  f"FROZEN GEOMETRY: Do not touch stairs, railings, or window frames. Keep original pixels outside the mask. "
                  f"8k, photorealistic architectural render, matte mineral texture.")
    else:
        # Ригель: пропорция 1:12, экстремально тонкие кирпичи
        prompt = (f"{base} {zone_instr}Replace {target} with EXACTLY the provided texture. "
                  f"Format: Riegel brick (needle-thin, ultra-long). Ratio 1:12. "
                  f"Each brick must be exceptionally thin and needle-like. "
                  f"Apply with maximum horizontal density and razor-sharp, razor-thin linear courses.")

    if grout_color_hex and material_type not in _STONE_TYPES:
        prompt += f" Mortar joints color: HEX {grout_color_hex}."

    prompt += " Hyper-realistic, 8k architectural visualization, sharp focus on masonry."
    return prompt


def _derbent_zone_detail() -> str:
    """Детальное описание Melen Brutal 3D Rockface — для зональных промтов (plinth, belt)."""
    return (
        "Fill red zone with ultra-rugged Melen stone masonry matching the provided reference. "
        "RAZOR-SHARP TOP EDGE: The top row of stones must form a perfectly straight horizontal line, "
        "stopping exactly at the red mask limit. "
        "ZERO-BLEED: No stone relief or texture is allowed to protrude upwards past the boundary. "
        "FLUSH TOP: No protruding cap or ledge at the top row. "
        "SCALE: Small stones relative to windows. "
        "Heavy 3D relief — each stone protrudes at various depths (3-6cm), jagged rock-face texture, "
        "sharp irregular edges, deep dark crevices. Dry-stack, no mortar lines. "
        "FORBIDDEN: NO flat surfaces, NO smooth planes, NO bricks, NO tiles. "
    )


def _solid_zone_detail() -> str:
    """Детальное описание сплошной (бесшовной) текстуры — для зональных промтов."""
    return (
        "Fill the red zone with the provided texture as a CONTINUOUS, SEAMLESS surface. "
        "Strictly NO joints, NO seams, NO mortar lines, NO blocks, NO bricks, NO panel edges, NO repetition. "
        "Treat the source as a single uninterrupted coat that wraps the entire zone. "
        "Allow only gentle, organic colour/tonal variation; never produce a tiled or grid look. "
        "Match the matte/glossy character of the sample exactly. "
    )


def build_plinth_prompt(material_type: str = None) -> str:
    """Промт для подвкладки Цоколь."""
    if material_type == "derbent_stone":
        fill = _derbent_zone_detail()
    elif material_type == "solid":
        fill = _solid_zone_detail()
    else:
        fill = (
            "Replace the texture STRICTLY within the red zone with the provided texture. "
            "Ensure the new material (stone/brick) aligns with the perspective of the building. "
        )
    return (
        "SURGICAL IN-PAINTING TASK: Replace texture STRICTLY and ONLY within the red-highlighted zone. "
        "STRICT PIXEL-PERFECT BOUNDARY (CRITICAL): The new texture must stop exactly at the top boundary of the red line. "
        "Do not extend, bleed, or expand the plinth height by even a single pixel. "
        "CUT-BLOCK RULE: If a full stone block does not fit within the red zone, it MUST be cut or cropped exactly at the red line. "
        "Priority is 100% boundary accuracy over block integrity. Simply fill the shaded area and nowhere else. "
        "FROZEN GEOMETRY: Absolutely do NOT alter, remove, move, or simplify stairs, steps, railings, windows, or the ground. "
        "Every pixel outside the red zone must be preserved exactly as it appears in the original image. "
        "FLUSH TRANSITION: The top edge must be perfectly flush with the wall. "
        "Strictly forbid any decorative caps, ledges, trim, or protrusions at the upper boundary. " +
        fill +
        "8k, photorealistic architectural rendering."
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
                      belt_mode: str = "soldier", cornice: str = "no") -> str:
    """Промт для подвкладки Пояса.
    Сценарий А (has_texture=True): заменить зону на выбранную текстуру.
    Сценарий Б (has_texture=False, belt_mode='soldier'): солдатский ряд.
    Сценарий В (has_texture=False, belt_mode='chess'): шахматная вертикальная кладка.
    cornice: 'yes' — добавить выступающий карниз сверху, 'no' — без карниза (заподлицо).
    """
    base_constraint = (
        "STRICT ARCHITECTURAL PRESERVATION: Frozen geometry outside the mask. "
        "UNIT WIDTH LOCK (CRITICAL): The width of each vertical brick in the belt must be EXACTLY EQUAL "
        "to the height of the horizontal bricks on the adjacent wall. "
        "VISUAL CONTINUITY: Mortar joints in the belt must align perfectly with the thickness of mortar joints on the facade. "
        "CROP-ONLY MODE: Strictly forbid scaling. If the brick length is 300mm and the belt is 200mm, "
        "the model MUST show a 200mm segment of the brick. DO NOT SQUEEZE. "
        "ZERO-BLEED: Ensure the pattern is mathematically clipped by the red zone. "
    )

    cornice_instr = (
        "CORNICE: Add a clearly visible decorative cornice along the top edge of the belt — "
        "a horizontal protruding moulding/ledge (3-6 cm depth) casting a soft drop-shadow on the wall below. "
        "The cornice profile must be straight, continuous, and match the architectural style of the facade. "
        if cornice == "yes" else
        "NO CORNICE: The top edge of the belt must be perfectly flush with the wall plane. "
        "Strictly no protruding moulding, no ledge, no cap, no overhang at the top boundary. "
    )

    if has_texture:
        if material_type in ("textured_stone", "derbent_stone"):
            fill = _derbent_zone_detail()
        elif material_type == "solid":
            fill = _solid_zone_detail()
        else:
            fill = (
                "Replace the texture STRICTLY within the red zone with the provided material. "
                "FORCE ORIENTATION (CRITICAL): Ignore the orientation of the sample image. Rotate the material units 90 degrees vertically. "
                "MATCH SCALE: The short side of the vertical unit in the belt must match the thickness of the horizontal bricks on the main wall. "
                "CLIPPING ONLY: Do not apply texture a single pixel outside the mask. "
            )
        return (
            base_constraint +
            "The red-highlighted horizontal zone is a decorative belt. " +
            fill +
            cornice_instr +
            "8k, photorealistic."
        )
    elif belt_mode == "chess":
        return (
            base_constraint +
            "TASK: Vertical Staggered pattern. "
            "NO NARROWING: Maintain the full brick proportions. The bricks must look like the wall bricks just turned 90 degrees. "
            "STAGGER: Offset adjacent columns by exactly half a brick height. "
            "MASK CLIPPING: Strictly clip the pattern at the red line edges. No expansion beyond the belt. "
            "MATERIAL MATCH: Use the EXACT same color, tone, and material as the surrounding wall. " +
            cornice_instr +
            "Hyper-realistic architectural rendering, 8k, photorealistic lighting."
        )
    else:
        return (
            base_constraint +
            "TASK: Vertical Soldier Course. "
            "DIMENSION LOCK: Vertical bricks must have the same thickness as the facade bricks. "
            "CROP, DON'T SCALE: If the belt is 20cm high and the brick is 30cm, show a 20cm 'cut' brick. "
            "Do NOT compress the 30cm brick into 20cm. "
            "ZERO DEPTH: Stay perfectly flush with the wall plane. No recession, no 'sunken' effect. "
            "MATERIAL MATCH: Use the EXACT same color, tone, and material as the surrounding wall. " +
            cornice_instr +
            "Hyper-realistic architectural rendering, 8k, photorealistic lighting."
        )


async def generate_image(image_urls: list, prompt: str,
                         material_type: str | None = None) -> dict:
    """
    Отправляет запрос к нейросети и возвращает {'output_url': url}.
    image_urls — список URL изображений (обычно [photo_url] или [photo_url, texture_url]).
    material_type — определяет какой API использовать:
      standard/rigel → Nano Banana 2 (чёткие кирпичные паттерны)
      остальные      → GPT Image 2 (натуральный камень и пр.)
    Использует асинхронный режим с опросом статуса.
    """
    api_key = os.getenv("GEN_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise Exception("Не задан API ключ (ожидается GEN_API_KEY или API_KEY)")

    endpoint = _select_endpoint(material_type)

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
                "GenAPI POST start endpoint=%s material_type=%s quality=%s num_images=%s num_image_urls=%s",
                endpoint,
                material_type,
                payload.get("quality"),
                payload.get("num_images"),
                len(image_urls),
            )
            t0 = time.monotonic()
            resp = await client.post(endpoint, json=payload, headers=headers)
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
