"""Chat with Surya Sach in your terminal: same brain as the WhatsApp bot, no AWS or WhatsApp needed.

    python app/local_chat.py            # live weather, local files for storage
    /img path\\to\\inverter.jpg [caption]   send a photo (needs AWS credentials for OCR, else type the number)
    /quit
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bot.adapters import ConsoleMessenger, LiveWeather, LocalBlob  # noqa: E402
from bot.service import Deps, Service  # noqa: E402
from engine.solar import IST  # noqa: E402
from reader.ocr import read_photo  # noqa: E402
from store.stores import MemoryStore  # noqa: E402

STATE = Path("out/local_state.json")


def load_store() -> MemoryStore:
    s = MemoryStore()
    if STATE.exists():
        d = json.loads(STATE.read_text(encoding="utf-8"))
        s.profiles, s.readings, s.packs = d["profiles"], d["readings"], d["packs"]
    return s


def save_store(s: MemoryStore) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps({"profiles": s.profiles, "readings": s.readings, "packs": s.packs}),
                     encoding="utf-8")


def ocr(img: bytes, bill: bool) -> dict:
    try:
        import boto3
        return read_photo(img, boto3.client("textract", region_name="ap-south-1"),
                          boto3.client("bedrock-runtime", region_name="ap-south-1"), want_bill=bill)
    except Exception as e:  # noqa: BLE001
        print(f"(OCR unavailable locally: {e.__class__.__name__}. Type the number instead.)")
        return {"kind": "unknown", "value": None, "engine": "none"}


def main():
    store, media = load_store(), {}
    msgr = ConsoleMessenger("out", media)
    deps = Deps(store=store, msgr=msgr, weather=LiveWeather(), read_photo=ocr, blob=LocalBlob(),
                verify_base_url="http://localhost:8765", now=lambda: datetime.now(IST))
    svc, phone, n = Service(deps), "919999900001", 0
    print("Surya Sach local chat. Say hi. (/img <file> [caption], /quit)")
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line == "/quit":
            break
        n += 1
        msg = {"id": f"local-{datetime.now().timestamp()}-{n}", "from": phone, "type": "text", "text": line}
        if line.startswith("/img "):
            parts = line[5:].split(" ", 1)
            media[parts[0]] = parts[0]
            msg.update(type="image", media_id=parts[0], text=parts[1] if len(parts) > 1 else None)
        svc.process(msg)
        save_store(store)


if __name__ == "__main__":
    main()
