"""The WhatsApp conversation: onboarding, photo -> verdict, evidence pack, net-meter check."""
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from engine.diagnose import Day, diagnose
from engine.model import expected_band, promise_check
from engine.netmeter import check_net_meter
from engine.solar import IST
from evidence.pack import make_pack, sha256_bytes

from .i18n import ADVICE_HI, KIND, t

log = logging.getLogger("suryasach")

NUM_RE = re.compile(r"^\s*(\d{1,6}(?:[.,]\d{1,2})?)\s*(?:units?|kwh)?\s*$", re.I)
YES = {"yes", "y", "ok", "haan", "ha", "हाँ", "हां", "✅", "👍", "sahi", "सही"}
NO = {"no", "n", "nahi", "नहीं", "❌"}
REPORT = {"report", "pdf", "evidence", "pack", "रिपोर्ट", "सबूत"}
STATUS = {"status", "today", "latest", "result", "aaj", "आज", "स्थिति"}
HELP = {"help", "menu", "?", "मदद", "सहायता"}


@dataclass
class Deps:
    store: Any
    msgr: Any                      # send_text, send_document, send_audio, download_media
    weather: Any                   # fetch_om, fetch_nasa, site_climate, geocode
    read_photo: Callable           # (image_bytes, want_bill) -> dict
    blob: Any                      # put(key, data, content_type) -> key
    verify_base_url: str = "http://localhost:8000"
    tts: Callable | None = None    # (text, lang) -> mp3 bytes
    advisor: Callable | None = None  # (profile, text, deps) -> str
    dashboard_token: Callable | None = None  # (phone) -> token
    now: Callable[[], datetime] = field(default=lambda: datetime.now(IST))


def new_profile(phone: str) -> dict:
    return {"phone": phone, "state": "LOC", "lang": "en", "voice": False, "azimuth": 180,
            "tariff": 8.0, "pending": None, "last_total": None, "greeted": False}


def default_tilt(lat: float) -> int:
    return int(round(min(max(lat, 12), 30)))


def _num(text: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else None


class Service:
    def __init__(self, deps: Deps):
        self.d = deps

    # ------------------------------------------------------------- entry
    def process(self, msg: dict) -> None:
        if not self.d.store.mark_seen(msg["id"]):
            return
        phone = msg["from"]
        p = self.d.store.get_profile(phone) or new_profile(phone)
        p["last_seen"] = msg.get("ts") or int(self.d.now().timestamp())
        try:
            self._dispatch(p, msg)
        except Exception:  # noqa: BLE001 - never leave the user hanging
            log.exception("process failed")
            self._say(p, t(p["lang"], "error"), voice=False)
        self.d.store.put_profile(p)

    def _say(self, p, text, voice=True):
        self.d.msgr.send_text(p["phone"], text)
        if voice and p.get("voice") and self.d.tts:
            try:
                spoken = re.sub(r"[*_]", "", text)
                self.d.msgr.send_audio(p["phone"], self.d.tts(spoken, p["lang"]), "audio/mpeg")
            except Exception:  # noqa: BLE001 - voice is a bonus, never fatal
                log.exception("tts failed")

    # ------------------------------------------------------------ dispatch
    def _dispatch(self, p, msg):
        kind = msg.get("type", "text")
        text = (msg.get("text") or "").strip()
        low = text.lower()
        lang = p["lang"]

        if low in ("reset", "clear profile"):
            p.clear()
            p.update(new_profile(msg["from"]))
            p["greeted"] = True
            return self._say(p, t("en", "reset_ok"), voice=False)
        if low in ("hindi", "हिंदी", "hindi me", "हिन्दी"):
            p["lang"] = "hi"
            return self._say(p, t("hi", "lang_set"))
        if low in ("english", "angrezi", "अंग्रेज़ी"):
            p["lang"] = "en"
            return self._say(p, t("en", "lang_set"))
        if low in ("voice on", "voice off"):
            p["voice"] = low.endswith("on")
            return self._say(p, t(lang, "voice_on" if p["voice"] else "voice_off"), voice=False)
        if low.split(" ")[0] in ("promise", "check", "वादा") and kind == "text":
            return self._cmd_promise(p, text)
        if low in HELP:
            return self._say(p, t(lang, "help"))

        if not p["greeted"]:
            p["greeted"] = True
            if kind not in ("location",) and p["state"] == "LOC":
                self._say(p, t(lang, "welcome"), voice=False)
                if not _is_place_text(text):
                    return

        if p["state"] != "READY":
            return self._onboard(p, msg)

        if kind == "location":
            self._set_location(p, msg["lat"], msg["lon"], msg.get("name"))
            return self._say(p, t(lang, "ok"))
        if kind == "image":
            return self._on_photo(p, msg)
        if kind == "audio":
            return self._say(p, t(lang, "unknown"))

        if p.get("pending"):
            if low in YES:
                return self._commit_pending(p)
            if low in NO:
                p["pending"] = None
                return self._say(p, t(lang, "pending_cancel"))
        m = NUM_RE.match(text)
        if m:
            return self._on_number(p, float(m.group(1).replace(",", ".")), "text", None, None)
        if low in REPORT:
            return self._cmd_report(p)
        if low in ("dashboard", "dash", "डैशबोर्ड") and self.d.dashboard_token:
            url = f"{self.d.verify_base_url.rstrip('/')}/dashboard.html?t={self.d.dashboard_token(p['phone'])}"
            return self._say(p, t(lang, "dashboard_link", url=url), voice=False)
        if low in STATUS:
            return self._cmd_status(p)
        if low.startswith("bill") or low.startswith("export"):
            return self._cmd_bill(p, text)
        if self.d.advisor:
            return self._say(p, self.d.advisor(p, text, self.d))
        return self._say(p, t(lang, "unknown"))

    # ---------------------------------------------------------- onboarding
    def _set_location(self, p, lat, lon, place=None):
        p["lat"], p["lon"] = round(lat, 4), round(lon, 4)
        p["place"] = place or f"{lat:.2f}, {lon:.2f}"
        p["tilt"] = default_tilt(lat)

    def _onboard(self, p, msg):
        lang, st = p["lang"], p["state"]
        text = (msg.get("text") or "").strip()
        low = text.lower()
        if st == "LOC":
            if msg.get("type") == "location":
                self._set_location(p, msg["lat"], msg["lon"], msg.get("name"))
            else:
                g = self.d.weather.geocode(text) if text else None
                if not g:
                    return self._say(p, t(lang, "loc_fail"), voice=False)
                self._set_location(p, g["lat"], g["lon"], g["place"])
            p["state"] = "KWP"
            return self._say(p, t(lang, "loc_ok", place=p["place"]), voice=False)
        if st == "KWP":
            v = _num(text)
            if v is None or not 0.3 <= v <= 100:
                return self._say(p, t(lang, "bad_num"), voice=False)
            p["kwp"] = v
            p["state"] = "VENDOR"
            return self._say(p, t(lang, "ask_vendor"), voice=False)
        if st == "VENDOR":
            p["vendor"] = None if low == "skip" else text[:60]
            p["state"] = "PROMISED"
            return self._say(p, t(lang, "ask_promised"), voice=False)
        if st == "PROMISED":
            if low != "skip":
                v = _num(text)
                if v is None:
                    return self._say(p, t(lang, "bad_num"), voice=False)
                p["promised_kwh"] = v
            p["state"] = "TARIFF"
            return self._say(p, t(lang, "ask_tariff"), voice=False)
        if st == "TARIFF":
            if low != "skip":
                v = _num(text)
                if v is None or not 1 <= v <= 30:
                    return self._say(p, t(lang, "bad_num"), voice=False)
                p["tariff"] = v
            p["state"] = "READY"
            if p.get("promised_kwh"):
                self._say(p, self._promise_text(p, p["kwp"], p["promised_kwh"]), voice=False)
            return self._say(p, t(lang, "onboarded"))

    # --------------------------------------------------------- promise check
    def _promise_text(self, p, kwp, promised):
        lang = p["lang"]
        clim = self.d.weather.site_climate(p["lat"], p["lon"]) if p.get("lat") is not None else \
            {"annual_psh": 5.4, "best_month_psh": 6.8}
        pc = promise_check(kwp, promised, clim["annual_psh"], clim["best_month_psh"])
        p["promise_check"] = pc
        key = {"impossible": "promise_impossible", "peak_only": "promise_peak", "possible": "promise_ok"}[pc["verdict"]]
        return t(lang, key, kwp=kwp, promised=promised, ceiling=pc["ceiling_kwh_day"],
                 peak=pc["peak_month_ceiling_kwh_day"], typical=pc["typical_kwh_day"])

    def _cmd_promise(self, p, text):
        nums = re.findall(r"\d+(?:[.,]\d+)?", text)
        lang = p["lang"]
        if len(nums) >= 2:
            kwp, promised = (float(n.replace(",", ".")) for n in nums[:2])
            return self._say(p, self._promise_text(p, kwp, promised))
        if p.get("kwp") and p.get("promised_kwh"):
            return self._say(p, self._promise_text(p, p["kwp"], p["promised_kwh"]))
        return self._say(p, t(lang, "promise_usage"))

    # ------------------------------------------------------------- readings
    def _reading_date(self) -> date:
        now = self.d.now()
        return now.date() if now.hour >= 17 else now.date() - timedelta(days=1)

    def _on_photo(self, p, msg):
        lang = p["lang"]
        data, mime = self.d.msgr.download_media(msg["media_id"])
        sha = sha256_bytes(data)
        want_bill = "bill" in (msg.get("text") or "").lower()
        day = self._reading_date()
        key = self.d.blob.put(f"photos/{p['phone']}/{day.isoformat()}-{sha[:10]}.jpg", data, mime)
        res = self.d.read_photo(data, want_bill)
        if res["kind"] == "bill":
            return self._finish_bill(p, res.get("export_units"), None)
        if res["kind"] == "unknown" or res.get("value") is None:
            return self._say(p, t(lang, "photo_unreadable"))
        p["pending"] = {"value": res["value"], "kind": res["kind"], "date": day.isoformat(),
                        "photo_key": key, "sha": sha, "source": "photo"}
        return self._say(p, t(lang, "photo_read", value=res["value"], kind=KIND[lang][res["kind"]],
                              date=day.strftime("%d %b"), engine=_engine_name(res["engine"])), voice=False)

    def _commit_pending(self, p):
        pe = p["pending"]
        p["pending"] = None
        self._on_number(p, pe["value"], pe["source"], pe["photo_key"], pe["sha"],
                        kind=pe["kind"], day=date.fromisoformat(pe["date"]))

    def _on_number(self, p, value, source, photo_key, sha, kind=None, day=None):
        lang = p["lang"]
        day = day or self._reading_date()
        if p.get("pending") and source == "text":   # user typed a correction
            pe = p["pending"]
            photo_key, sha, day = pe["photo_key"], pe["sha"], date.fromisoformat(pe["date"])
            source = "photo-corrected"
            p["pending"] = None
        if kind is None:
            kind = "total" if value > max(p["kwp"] * 12, 60) else "daily"
            if kind == "total":
                self._say(p, t(lang, "suspicious_total", value=value), voice=False)
        if kind == "total":
            return self._on_total(p, value, day, photo_key, sha, source)
        self._record(p, day, value, source, photo_key, sha)
        self._reply_verdict(p, day)

    def _on_total(self, p, total, day, photo_key, sha, source):
        lang = p["lang"]
        last = p.get("last_total")
        p["last_total"] = {"date": day.isoformat(), "value": total}
        if not last:
            return self._say(p, t(lang, "need_total_again", value=total))
        gap = (day - date.fromisoformat(last["date"])).days
        delta = total - last["value"]
        if gap <= 0 or delta < 0:
            p["last_total"] = last
            return self._say(p, t(lang, "total_bad"))
        per_day = round(delta / gap, 2)
        for i in range(gap):
            d = date.fromisoformat(last["date"]) + timedelta(days=i + 1)
            self._record(p, d, per_day, source, photo_key if d == day else None,
                         sha if d == day else None, estimated=gap > 1)
        self._reply_verdict(p, day)

    def _weather_for(self, p, days: list[date]) -> dict:
        try:
            return self.d.weather.fetch_om(p["lat"], p["lon"], p["tilt"], p["azimuth"])
        except Exception:  # noqa: BLE001
            log.exception("weather fetch failed")
            return {}

    def _record(self, p, day, actual, source, photo_key, sha, estimated=False):
        wx = self._weather_for(p, [day]).get(day.isoformat())
        r = {"date": day.isoformat(), "actual": float(actual), "source": source,
             "photo_key": photo_key, "photo_sha256": sha, "estimated": estimated,
             "simulated": bool(p.get("sim"))}
        if wx:
            band = expected_band(p["kwp"], wx["h_poa"], wx["t_amb"])
            mid = (band.low + band.high) / 2
            r.update(h_poa=wx["h_poa"], ghi=wx["ghi"], rain_mm=wx["rain_mm"], t_amb=wx["t_amb"],
                     clear_sky_h=wx["clear_sky_h"], band_low=band.low, band_high=band.high,
                     ratio=round(actual / mid, 3) if mid else None)
        self.d.store.put_reading(p["phone"], r)
        return r

    def _days(self, p, limit=30) -> list[Day]:
        rows = self.d.store.list_readings(p["phone"])[-limit:]
        return [Day(r["date"], r["actual"], r["h_poa"], r.get("rain_mm", 0), r.get("t_amb", 30),
                    r.get("clear_sky_h", 0)) for r in rows if r.get("h_poa")]

    def _reply_verdict(self, p, day):
        lang = p["lang"]
        r = self.d.store.get_reading(p["phone"], day.isoformat())
        days = self._days(p)
        if not r or not r.get("h_poa") or not days:
            return self._say(p, t(lang, "saved_no_weather", actual=r["actual"], date=day.strftime("%d %b")))
        dg = diagnose(days, p["kwp"], p.get("tariff", 8.0))
        p["last_diagnosis"] = {k: dg[k] for k in ("date", "status", "shortfall_pct", "lost_inr_month")}
        icon = "✅" if dg["status"] == "healthy" else "⚠️"
        lines = [t(lang, "verdict_head", icon=icon, date=day.strftime("%d %b"), kwp=p["kwp"],
                   place=(p.get("place") or "").split(",")[0]),
                 t(lang, "verdict_made", actual=f"{r['actual']:.1f}"),
                 t(lang, "verdict_band", lo=f"{r['band_low']:.1f}", hi=f"{r['band_high']:.1f}",
                   ghi=f"{r['h_poa']:.1f}")]
        if dg["status"] == "short":
            lines.append(t(lang, "verdict_short", pct=round(dg["shortfall_pct"]), inr=f"{dg['lost_inr_month']:,}"))
            if dg["findings"]:
                f = dg["findings"][0]
                lines.append(ADVICE_HI.get(f["cause"], f["advice"]) if lang == "hi" else f["advice"])
            lines.append(t(lang, "verdict_report"))
        else:
            lines.append(t(lang, "verdict_ok"))
        self._say(p, "\n".join(lines))

    # --------------------------------------------------------------- commands
    def _cmd_status(self, p):
        rows = self.d.store.list_readings(p["phone"])
        if not rows:
            return self._say(p, t(p["lang"], "status_none"))
        self._reply_verdict(p, date.fromisoformat(rows[-1]["date"]))

    def _cmd_report(self, p):
        lang = p["lang"]
        rows = [r for r in self.d.store.list_readings(p["phone"])[-30:] if r.get("band_low") is not None]
        if len(rows) < 3:
            return self._say(p, t(lang, "pack_need", n=len(rows)))
        dg = diagnose(self._days(p), p["kwp"], p.get("tariff", 8.0))
        pack_id, manifest, pdf = make_pack(p, rows, dg, p.get("promise_check"), self.d.verify_base_url)
        self.d.blob.put(f"packs/{pack_id}.pdf", pdf, "application/pdf")
        self.d.store.put_pack({"id": pack_id, "manifest": manifest, "pdf_sha256": sha256_bytes(pdf),
                               "pdf_key": f"packs/{pack_id}.pdf"})
        url = f"{self.d.verify_base_url.rstrip('/')}/verify.html?id={pack_id}"
        self.d.msgr.send_document(p["phone"], pdf, f"surya-sach-evidence-{pack_id[:8]}.pdf",
                                  t(lang, "pack_caption", url=url))

    def _cmd_bill(self, p, text):
        m = re.search(r"export\D*(\d+(?:\.\d+)?)", text, re.I)
        if not m:
            return self._say(p, t(p["lang"], "bill_ask"))
        d = re.search(r"days?\D*(\d+)", text, re.I)
        self._finish_bill(p, float(m.group(1)), int(d.group(1)) if d else 30)

    def _finish_bill(self, p, exported, days):
        lang = p["lang"]
        days = days or 30
        end = self.d.now().date().isoformat()
        start = (self.d.now().date() - timedelta(days=days)).isoformat()
        rows = self.d.store.list_readings(p["phone"], start, end)
        if len(rows) < 3 or exported is None:
            return self._say(p, t(lang, "bill_need_data"))
        gen = sum(r["actual"] for r in rows) / len(rows) * days   # scale recorded days to the bill period
        res = check_net_meter(gen, exported, days)
        self._say(p, t(lang, "bill_result", gen=round(gen), days=days, exp=round(exported), note=res["note"]))


def _is_place_text(text: str) -> bool:
    low = text.lower()
    return bool(text) and low not in ("hi", "hello", "hey", "namaste", "नमस्ते", "start") and len(text) > 2


def _engine_name(e: str) -> str:
    return {"textract": "Amazon Textract", "bedrock-vision": "Amazon Bedrock vision"}.get(e, e)
