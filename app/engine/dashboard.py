"""Shape one system's data for the web dashboard (shared by the API and the offline sample)."""
import hashlib
import hmac

from engine.diagnose import Day, diagnose


def dashboard_payload(profile: dict, readings: list[dict]) -> dict:
    rows = [r for r in readings if r.get("band_low") is not None][-45:]
    days = [Day(r["date"], r["actual"], r["h_poa"], r.get("rain_mm", 0), r.get("t_amb", 30),
                r.get("clear_sky_h", 0)) for r in rows if r.get("h_poa")]
    dg = diagnose(days, profile["kwp"], profile.get("tariff", 8.0)) if days else None
    return {
        "id": profile["phone"],
        "name": profile.get("name") or "Your roof",
        "place": profile.get("place"), "kwp": profile["kwp"], "tilt": profile.get("tilt"),
        "vendor": profile.get("vendor"), "promised_kwh": profile.get("promised_kwh"),
        "tariff": profile.get("tariff", 8.0), "simulated": bool(profile.get("sim")),
        "promise_check": profile.get("promise_check"),
        "readings": [{k: r.get(k) for k in ("date", "actual", "band_low", "band_high", "ratio",
                                            "rain_mm", "ghi", "ghi_nasa", "source", "photo_sha256")}
                     for r in rows],
        "diagnosis": ({k: dg[k] for k in ("date", "status", "shortfall_pct", "lost_inr_month", "findings")}
                      if dg else None),
    }


def make_token(secret: str, phone: str) -> str:
    sig = hmac.new(secret.encode(), phone.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{phone}.{sig}"


def read_token(secret: str, token: str) -> str | None:
    phone, _, sig = (token or "").partition(".")
    good = hmac.new(secret.encode(), phone.encode(), hashlib.sha256).hexdigest()[:24]
    return phone if phone and hmac.compare_digest(sig, good) else None
