"""Seed demonstration systems using REAL recent weather and SIMULATED faults.

Every seeded profile and reading is flagged simulated=True; the PDF, verify page and scorecard all say so.
Usage:  python -m seed.load                       # in-memory dry run, prints a summary
        python -m seed.load --table <TableName>   # write to DynamoDB
"""
import argparse
import random
from datetime import date

from bot.adapters import LiveWeather
from engine.model import expected_band, promise_check
from engine.scorecard import system_stat
from store.stores import DynamoStore, MemoryStore

# name, city, kwp, vendor, promised, fault model
SYSTEMS = [
    ("Ramesh", "Jaipur", 2.0, "SunnyRoofs Pvt Ltd", 13, "dust"),
    ("Sunita", "Lucknow", 3.0, "SunnyRoofs Pvt Ltd", 18, "dead"),
    ("Imran", "Jaipur", 2.0, "SunnyRoofs Pvt Ltd", 12, "shaded"),
    ("Priya", "Pune", 3.0, "BrightGrid Energy", 12, "healthy"),
    ("Arjun", "Ahmedabad", 5.0, "BrightGrid Energy", 20, "healthy"),
    ("Meena", "Indore", 3.0, "BrightGrid Energy", 12, "healthy"),
    ("Kabir", "Jaipur", 4.0, "BrightGrid Energy", 16, "healthy"),
    ("Neha", "Lucknow", 3.0, "GreenVolt Solutions", 14, "mid"),
    ("Dev", "Pune", 2.0, "GreenVolt Solutions", 9, "mid"),
    ("Anita", "Indore", 3.0, "GreenVolt Solutions", 15, "dust"),
]


def ratio_for(model: str, i: int, wash_reset: int, rng: random.Random) -> float:
    if model == "healthy":
        return rng.uniform(0.95, 1.05)
    if model == "mid":
        return rng.uniform(0.82, 0.92)
    if model == "dead":
        return rng.uniform(0.47, 0.54)
    if model == "shaded":
        return rng.uniform(0.66, 0.78)
    if model == "dust":                      # decays after each wash (rain >= 5 mm) or from day 0
        return max(0.55, 1.0 - 0.012 * (i - wash_reset)) * rng.uniform(0.97, 1.03)
    raise ValueError(model)


def seed(store, weather=None, seed_value=7, days=28):
    weather = weather or LiveWeather()
    rng = random.Random(seed_value)
    cache = {}
    out = []
    for n, (name, city, kwp, vendor, promised, model) in enumerate(SYSTEMS):
        g = weather.geocode(city)
        key = (city, round(min(max(g["lat"], 12), 30)))
        phone = f"9190000000{n:02d}"
        tilt = key[1]
        if key not in cache:
            cache[key] = weather.fetch_om(g["lat"], g["lon"], tilt, 180)
        wx = cache[key]
        profile = {"phone": phone, "state": "READY", "lang": "en", "voice": False, "azimuth": 180,
                   "tariff": 8.0, "pending": None, "last_total": None, "greeted": True, "sim": True,
                   "name": name, "place": g["place"], "lat": g["lat"], "lon": g["lon"], "tilt": tilt,
                   "kwp": kwp, "vendor": vendor, "promised_kwh": promised, "nudges": False}
        clim = weather.site_climate(g["lat"], g["lon"])
        profile["promise_check"] = promise_check(kwp, promised, clim["annual_psh"], clim["best_month_psh"])
        store.put_profile(profile)
        dates = sorted(wx)[-days:]
        wash = 0
        for i, d in enumerate(dates):
            w = wx[d]
            if w["rain_mm"] >= 5:
                wash = i
            band = expected_band(kwp, w["h_poa"], w["t_amb"])
            mid = (band.low + band.high) / 2
            r = ratio_for(model, i, wash, rng)
            actual = round(mid * r, 2)
            store.put_reading(phone, {
                "date": d, "actual": actual, "source": "simulated", "photo_key": None, "photo_sha256": None,
                "estimated": False, "simulated": True, "h_poa": w["h_poa"], "ghi": w["ghi"],
                "rain_mm": w["rain_mm"], "t_amb": w["t_amb"], "clear_sky_h": w["clear_sky_h"],
                "band_low": band.low, "band_high": band.high, "ratio": round(actual / mid, 3)})
        stat = system_stat(store.list_readings(phone))
        store.put_vendor_stat(vendor, phone, {**stat, "simulated": True})
        out.append((name, city, vendor, model, stat))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--table")
    a = ap.parse_args()
    if a.table:
        import boto3
        st = DynamoStore(boto3.resource("dynamodb").Table(a.table))
    else:
        st = MemoryStore()
    for row in seed(st):
        print(row)
    print("seeded", len(SYSTEMS), "simulated systems into", a.table or "memory", "on", date.today())
