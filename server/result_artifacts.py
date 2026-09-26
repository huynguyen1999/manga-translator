import json
from pathlib import Path
from typing import Any, Callable


def ensure_bbox_artifact(
    folder_path: Path,
    file_name: str,
    regions_data: list[dict] | None = None,
) -> bool:
    if file_name in {"bboxes.png", "bboxes_unfiltered.png"}:
        return False
    target_path = folder_path / file_name
    if target_path.is_file():
        return True

    if regions_data is None:
        text_regions_path = folder_path / "text_regions.json"
        if not text_regions_path.is_file():
            return False
        try:
            regions_data = json.loads(text_regions_path.read_text(encoding="utf-8"))
            if not isinstance(regions_data, list):
                return False
        except Exception:
            return False

    if file_name == "detection.json":
        payload = []
        for region in regions_data:
            lines = region.get("lines") or []
            confidence = region.get("confidence") if region.get("confidence") is not None else region.get("prob")
            if lines:
                payload.extend({"pts": line, **({"confidence": confidence} if confidence is not None else {})} for line in lines)
            else:
                x = region.get("x", 0)
                y = region.get("y", 0)
                w = region.get("width", 0)
                h = region.get("height", 0)
                item: dict[str, Any] = {"pts": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]}
                if confidence is not None:
                    item["confidence"] = confidence
                payload.append(item)
        try:
            target_path.write_text(json.dumps(payload), encoding="utf-8")
            return True
        except OSError:
            return False

    return False


def ensure_thumbnail_artifact(
    folder_path: Path,
    file_name: str,
    final_file: Callable[[Path], Path | None],
    input_file: Callable[[Path], Path | None],
    logger: Any,
) -> bool:
    if file_name not in {"thumbnail.webp", "thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg"}:
        return False

    target_path = (folder_path / file_name).resolve()
    if target_path.is_file() and target_path.stat().st_size > 0:
        return True

    source_path = final_file(folder_path)
    if source_path is None or not source_path.is_file() or source_path.stat().st_size == 0:
        input_path = input_file(folder_path)
        if input_path and input_path.is_file() and input_path.stat().st_size > 0:
            source_path = input_path
        else:
            return False

    try:
        from PIL import Image
        with Image.open(source_path) as img:
            mode = "RGBA" if "A" in img.getbands() else "RGB"
            converted = img.convert(mode)
            converted.thumbnail((360, 500), Image.Resampling.LANCZOS)
            lower_name = file_name.lower()
            if lower_name.endswith(".webp"):
                converted.save(target_path, format="WEBP", quality=80)
            elif lower_name.endswith((".jpg", ".jpeg")):
                if converted.mode == "RGBA":
                    converted = converted.convert("RGB")
                converted.save(target_path, format="JPEG", quality=82)
            else:
                converted.save(target_path, format="PNG")
            return True
    except Exception as err:
        logger.warning(f"Failed to synthesize thumbnail {file_name} for {folder_path.name}: {err}")
        return False
