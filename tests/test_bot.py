from datetime import datetime, timedelta

import pytest

from bot.service import Deps, Service
from engine.solar import IST
from evidence.pack import content_hash
from store.stores import MemoryStore

NOW = datetime(2026, 10, 11, 19, 0, tzinfo=IST)   # evening -> reading date is today


class FakeMsgr:
    def __init__(self):
        self.texts, self.docs, self.audio = [], [], []

    def send_text(self, to, text):
        self.texts.append(text)

    def send_document(self, to, data, filename, caption=""):
        self.docs.append((filename, data, caption))

    def send_audio(self, to, data, mime=""):
        self.audio.append(data)

    def download_media(self, media_id):
        return b"fake-jpeg-" + media_id.encode(), "image/jpeg"


class FakeWeather:
    def __init__(self, rain=None):
        self.rain = rain or {}

    def geocode(self, name):
        return {"lat": 26.91, "lon": 75.79, "place": "Jaipur, Rajasthan"} if "jaipur" in name.lower() else None

    def site_climate(self, lat, lon):
        return {"annual_psh": 5.5, "best_month_psh": 7.0}

    def fetch_om(self, lat, lon, tilt, az):
        out = {}
        for i in range(0, 36):
            d = (NOW.date() - timedelta(days=i)).isoformat()
            out[d] = {"h_poa": 5.8, "ghi": 5.6, "rain_mm": self.rain.get(i, 0.0), "t_amb": 32.0,
                      "clear_sky_h": 6.0, "k": 0.97}
        return out

    def fetch_nasa(self, *a):
        return {}


class FakeBlob:
    def __init__(self):
        self.items = {}

    def put(self, key, data, content_type=""):
        self.items[key] = data
        return key


def make(reader=None, weather=None):
    store, msgr, blob = MemoryStore(), FakeMsgr(), FakeBlob()
    deps = Deps(store=store, msgr=msgr, weather=weather or FakeWeather(),
                read_photo=reader or (lambda img, bill: {"kind": "daily", "value": 5.4,
                                                        "confidence": 0.9, "engine": "textract"}),
                blob=blob, verify_base_url="https://example.test", now=lambda: NOW,
                tts=lambda text, lang: b"mp3")
    return Service(deps), store, msgr, blob


_n = [0]


def say(svc, text=None, **kw):
    _n[0] += 1
    svc.process({"id": f"m{_n[0]}", "from": "919999912345", "type": kw.pop("type", "text"),
                 "text": text, **kw})


def onboard(svc, promised="13"):
    say(svc, "hi")
    say(svc, "Jaipur")
    say(svc, "2")
    say(svc, "SunnyRoofs Pvt Ltd")
    say(svc, promised)
    say(svc, "8")


def test_full_onboarding_runs_promise_check():
    svc, store, msgr, _ = make()
    onboard(svc)
    p = store.get_profile("919999912345")
    assert p["state"] == "READY" and p["kwp"] == 2 and p["vendor"].startswith("Sunny")
    joined = "\n".join(msgr.texts)
    assert "physically impossible" in joined
    assert p["promise_check"]["verdict"] == "impossible"


def test_photo_confirm_then_verdict_short():
    svc, store, msgr, blob = make()
    onboard(svc)
    say(svc, None, type="image", media_id="abc")
    assert "Reply *yes*" in msgr.texts[-1]
    say(svc, "yes")
    out = msgr.texts[-1]
    assert "short" in out and "5.4" in out
    r = store.get_reading("919999912345", NOW.date().isoformat())
    assert r["photo_sha256"] and r["actual"] == 5.4 and r["ratio"] < 0.8
    assert any(k.startswith("photos/") for k in blob.items)


def test_typed_correction_of_pending_reading():
    svc, store, msgr, _ = make()
    onboard(svc)
    say(svc, None, type="image", media_id="abc")
    say(svc, "6.5")
    r = store.get_reading("919999912345", NOW.date().isoformat())
    assert r["actual"] == 6.5 and r["source"] == "photo-corrected"


def test_unreadable_photo():
    svc, store, msgr, _ = make(reader=lambda i, b: {"kind": "unknown", "value": None, "engine": "none"})
    onboard(svc)
    say(svc, None, type="image", media_id="x")
    assert "could not read" in msgr.texts[-1]


def test_idempotent_message_ids():
    svc, store, msgr, _ = make()
    svc.process({"id": "dup", "from": "919999912345", "type": "text", "text": "hi"})
    n = len(msgr.texts)
    svc.process({"id": "dup", "from": "919999912345", "type": "text", "text": "hi"})
    assert len(msgr.texts) == n


def test_evidence_pack_end_to_end():
    svc, store, msgr, blob = make()
    onboard(svc)
    p = store.get_profile("919999912345")
    for i in range(1, 6):
        d = (NOW.date() - timedelta(days=i)).isoformat()
        svc._record(p, NOW.date() - timedelta(days=i), 5.5, "text", None, None)
    say(svc, "report")
    assert msgr.docs, msgr.texts[-1]
    name, pdf, caption = msgr.docs[-1]
    assert pdf.startswith(b"%PDF") and "verify.html?id=" in caption
    pack_id = caption.split("id=")[1]
    meta = store.get_pack(pack_id)
    assert content_hash(meta["manifest"]) == pack_id        # tamper-evident


def test_report_needs_data():
    svc, store, msgr, _ = make()
    onboard(svc)
    say(svc, "report")
    assert "at least 3 days" in msgr.texts[-1]


def test_standalone_promise_command():
    svc, store, msgr, _ = make()
    say(svc, "promise 2 13")
    assert "Promise check" in msgr.texts[-1]


def test_net_meter_flags_zero_export():
    svc, store, msgr, _ = make()
    onboard(svc)
    p = store.get_profile("919999912345")
    for i in range(1, 11):
        svc._record(p, NOW.date() - timedelta(days=i), 8.0, "text", None, None)
    say(svc, "export 0 days 30")
    assert "net meter may not be" in msgr.texts[-1] or "meter may not be" in msgr.texts[-1]


def test_lifetime_counter_needs_two_readings():
    svc, store, msgr, _ = make()
    onboard(svc)
    say(svc, "1234.5")
    assert "Saved your meter counter" in msgr.texts[-1]


def test_hindi_and_voice():
    svc, store, msgr, _ = make()
    onboard(svc)
    say(svc, "hindi")
    say(svc, "voice on")
    say(svc, "5.4")
    assert "यूनिट" in msgr.texts[-1] and msgr.audio


def test_weather_failure_still_saves():
    class Broken(FakeWeather):
        def fetch_om(self, *a):
            raise RuntimeError("down")

    svc, store, msgr, _ = make(weather=Broken())
    onboard(svc)
    say(svc, "5.4")
    assert store.get_reading("919999912345", NOW.date().isoformat())["actual"] == 5.4
    assert "Weather data is not available" in msgr.texts[-1]


def test_dashboard_command_sends_private_link():
    svc, store, msgr, _ = make()
    svc.d.dashboard_token = lambda phone: "TOK"
    onboard(svc)
    say(svc, "dashboard")
    assert "https://example.test/dashboard.html?t=TOK" in msgr.texts[-1]
