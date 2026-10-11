"""Photo -> energy reading. Textract first, Bedrock vision as the fallback for LCD / 7-segment screens."""
import json
import os
import re

from .parse import parse_energy

VISION_MODEL = os.environ.get("BEDROCK_VISION_MODEL", "apac.amazon.nova-lite-v1:0")
CONFIDENT = 0.8

VISION_PROMPT = (
    "This is a photo of a solar inverter display, energy meter, or electricity bill. "
    "Reply with JSON only, no prose: "
    '{"kind": "daily" | "total" | "bill" | "unknown", "value": number or null, '
    '"export_units": number or null, "import_units": number or null}. '
    "kind=daily is today's generated energy in kWh; kind=total is the lifetime kWh counter; "
    "kind=bill is an electricity bill, in which case fill export_units/import_units if visible. "
    "If you cannot read it confidently use kind=unknown."
)


def textract_text(image: bytes, client) -> str:
    res = client.detect_document_text(Document={"Bytes": image})
    return "\n".join(b["Text"] for b in res.get("Blocks", []) if b["BlockType"] == "LINE")


def vision_read(image: bytes, client, media_type: str = "jpeg") -> dict | None:
    res = client.converse(
        modelId=VISION_MODEL,
        messages=[{"role": "user", "content": [
            {"image": {"format": media_type, "source": {"bytes": image}}},
            {"text": VISION_PROMPT},
        ]}],
        inferenceConfig={"maxTokens": 200, "temperature": 0},
    )
    text = res["output"]["message"]["content"][0]["text"]
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def read_photo(image: bytes, textract, bedrock, want_bill: bool = False) -> dict:
    """Return {'kind','value','confidence','engine'} plus bill fields. kind='unknown' if unreadable."""
    if not want_bill:
        try:
            parsed = parse_energy(textract_text(image, textract))
        except Exception:  # noqa: BLE001 - OCR service hiccup should fall through to vision
            parsed = None
        if parsed and parsed["confidence"] >= CONFIDENT:
            return {**parsed, "engine": "textract"}
    try:
        v = vision_read(image, bedrock)
    except Exception:  # noqa: BLE001
        v = None
    if v and v.get("kind") in ("daily", "total", "bill"):
        return {"kind": v["kind"], "value": v.get("value"), "confidence": 0.75,
                "export_units": v.get("export_units"), "import_units": v.get("import_units"),
                "engine": "bedrock-vision"}
    if not want_bill and parsed:
        return {**parsed, "engine": "textract"}
    return {"kind": "unknown", "value": None, "confidence": 0.0, "engine": "none"}
