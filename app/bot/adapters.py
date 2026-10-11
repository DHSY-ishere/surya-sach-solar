"""Real-world adapters: WhatsApp Cloud API, S3 blobs, Polly voice, live weather, console messenger."""
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from weather import sources

GRAPH = "https://graph.facebook.com/v21.0"


# ------------------------------------------------------------------ WhatsApp
class WhatsApp:
    def __init__(self, token: str, phone_number_id: str):
        self.token, self.pnid = token, phone_number_id

    def _req(self, url, data=None, headers=None, method=None):
        h = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read(), r.headers.get("Content-Type", "")

    def _send(self, payload: dict):
        body, _ = self._req(f"{GRAPH}/{self.pnid}/messages",
                            json.dumps({"messaging_product": "whatsapp", **payload}).encode(),
                            {"Content-Type": "application/json"})
        return json.loads(body)

    def send_text(self, to, text):
        return self._send({"to": to, "type": "text", "text": {"body": text[:4000], "preview_url": False}})

    def _upload(self, data: bytes, mime: str, filename: str) -> str:
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'.encode(),
                 f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\n{mime}\r\n'.encode(),
                 (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                  f"Content-Type: {mime}\r\n\r\n").encode(), data, f"\r\n--{boundary}--\r\n".encode()]
        body, _ = self._req(f"{GRAPH}/{self.pnid}/media", b"".join(parts),
                            {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return json.loads(body)["id"]

    def send_document(self, to, data, filename, caption=""):
        mid = self._upload(data, "application/pdf", filename)
        return self._send({"to": to, "type": "document",
                           "document": {"id": mid, "filename": filename, "caption": caption[:1000]}})

    def send_audio(self, to, data, mime="audio/mpeg"):
        mid = self._upload(data, mime, "reply.mp3")
        return self._send({"to": to, "type": "audio", "audio": {"id": mid}})

    def send_template(self, to, name="hello_world", lang="en_US"):
        return self._send({"to": to, "type": "template",
                           "template": {"name": name, "language": {"code": lang}}})

    def download_media(self, media_id):
        meta, _ = self._req(f"{GRAPH}/{media_id}")
        info = json.loads(meta)
        data, _ = self._req(info["url"])
        return data, info.get("mime_type", "image/jpeg")


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    mac = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, header[7:])


def extract_messages(payload: dict) -> list[dict]:
    """Flatten a WhatsApp webhook payload into our simple message dicts."""
    out = []
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            for m in ch.get("value", {}).get("messages", []):
                msg = {"id": m["id"], "from": m["from"], "ts": int(m.get("timestamp", time.time())),
                       "type": m["type"], "text": None}
                if m["type"] == "text":
                    msg["text"] = m["text"]["body"]
                elif m["type"] == "image":
                    msg["media_id"] = m["image"]["id"]
                    msg["text"] = m["image"].get("caption")
                elif m["type"] == "location":
                    loc = m["location"]
                    msg.update(lat=loc["latitude"], lon=loc["longitude"], name=loc.get("name"))
                elif m["type"] == "interactive":
                    r = m["interactive"].get("button_reply") or m["interactive"].get("list_reply") or {}
                    msg["type"], msg["text"] = "text", r.get("title", "")
                elif m["type"] == "audio":
                    msg["media_id"] = m["audio"]["id"]
                out.append(msg)
    return out


# ------------------------------------------------------------------ blobs
class S3Blob:
    def __init__(self, s3, bucket):
        self.s3, self.bucket = s3, bucket

    def put(self, key, data, content_type="application/octet-stream"):
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get(self, key) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()


class LocalBlob:
    def __init__(self, root="out/blobs"):
        self.root = Path(root)

    def put(self, key, data, content_type=""):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key):
        return (self.root / key).read_bytes()


# ------------------------------------------------------------------ voice
def polly_tts(polly):
    voices = {"hi": ("Kajal", "hi-IN"), "en": ("Kajal", "en-IN")}

    def synth(text: str, lang: str) -> bytes:
        voice, code = voices.get(lang, voices["en"])
        res = polly.synthesize_speech(Text=text[:2900], VoiceId=voice, Engine="neural",
                                      LanguageCode=code, OutputFormat="mp3")
        return res["AudioStream"].read()

    return synth


# ------------------------------------------------------------------ weather
class LiveWeather:
    """Open-Meteo / NASA POWER with a short in-process cache (Lambda warm starts)."""

    def __init__(self, ttl=600):
        self.ttl, self._c = ttl, {}

    def _cached(self, key, fn):
        hit = self._c.get(key)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        val = fn()
        self._c[key] = (time.time(), val)
        return val

    def fetch_om(self, lat, lon, tilt, azimuth):
        return self._cached(("om", lat, lon, tilt, azimuth),
                            lambda: sources.fetch_open_meteo(lat, lon, tilt, azimuth))

    def fetch_nasa(self, lat, lon, start, end):
        return sources.fetch_nasa_daily(lat, lon, start, end)

    def site_climate(self, lat, lon):
        return self._cached(("clim", lat, lon), lambda: sources.fetch_site_climate(lat, lon))

    def geocode(self, name):
        return sources.geocode(name)


# ------------------------------------------------------------------ console (local demo)
class ConsoleMessenger:
    def __init__(self, out_dir="out", media: dict | None = None):
        import sys
        try:   # Windows consoles default to cp1252 and choke on emoji / Devanagari
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.media = media or {}
        self.log: list[tuple[str, str]] = []

    def send_text(self, to, text):
        self.log.append(("text", text))
        print(f"\n[bot -> {to}]\n{text}")

    def send_document(self, to, data, filename, caption=""):
        path = self.out / filename
        path.write_bytes(data)
        self.log.append(("document", str(path)))
        print(f"\n[bot -> {to}] document saved: {path}\n{caption}")

    def send_audio(self, to, data, mime="audio/mpeg"):
        path = self.out / f"voice-{len(self.log)}.mp3"
        path.write_bytes(data)
        self.log.append(("audio", str(path)))
        print(f"\n[bot -> {to}] voice note saved: {path}")

    def download_media(self, media_id):
        path = Path(self.media.get(media_id, media_id))
        return path.read_bytes(), "image/jpeg"
