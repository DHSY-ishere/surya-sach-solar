"""Irradiance and weather sources: Open-Meteo (fresh, drives the verdict) and
NASA POWER (lags ~5 days, acts as the independent auditor)."""
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from engine.solar import clear_sky_daily_kwh_m2

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
NASA_DAILY = "https://power.larc.nasa.gov/api/temporal/daily/point"
NASA_CLIM = "https://power.larc.nasa.gov/api/temporal/climatology/point"
DEFAULT_ANNUAL_PSH = 5.4
DEFAULT_BEST_MONTH_PSH = 6.8


def _get_json(url: str, params: dict, timeout: int = 40, retries: int = 3):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(full, timeout=timeout) as r:
                body = r.read()
            if body:
                return json.loads(body)
            last = ValueError("empty response")
        except Exception as e:  # noqa: BLE001 - network, retried then re-raised
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


# ---------------------------------------------------------------- Open-Meteo
def parse_open_meteo(payload: dict, lat: float, lon: float) -> dict[str, dict]:
    """Hourly -> per-day dict with h_poa, ghi, rain_mm, t_amb (daytime mean), clear_sky_h."""
    h = payload["hourly"]
    days: dict[str, dict] = {}
    for i, ts in enumerate(h["time"]):
        d = ts[:10]
        rec = days.setdefault(d, {"gti": 0.0, "ghi": 0.0, "rain": 0.0, "t": [], "n": 0})
        gti = h["global_tilted_irradiance"][i] or 0.0
        ghi = h["shortwave_radiation"][i] or 0.0
        rec["gti"] += gti
        rec["ghi"] += ghi
        rec["rain"] += h["precipitation"][i] or 0.0
        if ghi > 50 and h["temperature_2m"][i] is not None:
            rec["t"].append(h["temperature_2m"][i])
        rec["n"] += 1
    out = {}
    for d, r in days.items():
        if r["n"] < 24:
            continue
        h_poa = round(r["gti"] / 1000.0, 3)
        ghi_kwh = round(r["ghi"] / 1000.0, 3)
        clear_ghi = clear_sky_daily_kwh_m2(lat, lon, date.fromisoformat(d))
        k = min(1.2, ghi_kwh / clear_ghi) if clear_ghi else 1.0
        out[d] = {
            "h_poa": h_poa,
            "ghi": ghi_kwh,
            "rain_mm": round(r["rain"], 1),
            "t_amb": round(sum(r["t"]) / len(r["t"]), 1) if r["t"] else 30.0,
            "clear_sky_h": round(h_poa / k, 3) if k > 0.05 else h_poa,
            "k": round(k, 3),
        }
    return out


def fetch_open_meteo(lat: float, lon: float, tilt: float, azimuth_deg: float,
                     past_days: int = 35) -> dict[str, dict]:
    """azimuth_deg is compass bearing (180 = south); Open-Meteo wants 0 = south, -90 = east."""
    payload = _get_json(OPEN_METEO, {
        "latitude": lat, "longitude": lon,
        "hourly": "global_tilted_irradiance,shortwave_radiation,temperature_2m,precipitation",
        "tilt": round(tilt), "azimuth": round(azimuth_deg - 180),
        "past_days": min(past_days, 92), "forecast_days": 1,
        "timezone": "Asia/Kolkata",
    })
    return parse_open_meteo(payload, lat, lon)


# ---------------------------------------------------------------- NASA POWER
def parse_nasa_daily(payload: dict) -> dict[str, float]:
    raw = payload["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
    return {f"{k[:4]}-{k[4:6]}-{k[6:]}": v for k, v in raw.items() if v > -900}


def fetch_nasa_daily(lat: float, lon: float, start: date, end: date) -> dict[str, float]:
    payload = _get_json(NASA_DAILY, {
        "parameters": "ALLSKY_SFC_SW_DWN", "community": "RE",
        "longitude": lon, "latitude": lat,
        "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON",
    }, timeout=60)
    return parse_nasa_daily(payload)


def fetch_site_climate(lat: float, lon: float) -> dict:
    """Annual and best-month peak sun hours (GHI, kWh/m2/day) from NASA POWER climatology."""
    try:
        payload = _get_json(NASA_CLIM, {
            "parameters": "ALLSKY_SFC_SW_DWN", "community": "RE",
            "longitude": lon, "latitude": lat, "format": "JSON",
        }, timeout=60)
        m = payload["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
        months = [v for k, v in m.items() if k != "ANN" and v > 0]
        return {"annual_psh": round(m.get("ANN") or sum(months) / len(months), 2),
                "best_month_psh": round(max(months), 2), "source": "NASA POWER climatology"}
    except Exception:  # noqa: BLE001 - fall back to India-wide numbers, flagged in output
        return {"annual_psh": DEFAULT_ANNUAL_PSH, "best_month_psh": DEFAULT_BEST_MONTH_PSH,
                "source": "India-wide default (site data unavailable)"}


# ---------------------------------------------------------------- geocoding
def geocode(name: str) -> dict | None:
    try:
        res = _get_json(GEOCODE, {"name": name, "count": 1, "country_code": "IN"}, retries=2)
    except RuntimeError:
        return None
    hits = res.get("results") or []
    if not hits:
        return None
    h = hits[0]
    return {"lat": h["latitude"], "lon": h["longitude"],
            "place": ", ".join(x for x in (h["name"], h.get("admin1")) if x)}


def cross_check(open_meteo_ghi: float, nasa_ghi: float) -> dict:
    """Two independent datasets: how far apart are they?"""
    if not nasa_ghi:
        return {"agree": None, "diff_pct": None}
    diff = (open_meteo_ghi - nasa_ghi) / nasa_ghi * 100
    return {"agree": abs(diff) <= 8.0, "diff_pct": round(diff, 1)}
