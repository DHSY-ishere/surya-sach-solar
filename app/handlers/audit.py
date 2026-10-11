"""Daily auditor: backfill NASA POWER irradiance (5-day lag) onto readings and refresh vendor stats."""
import logging
from datetime import datetime, timedelta

from bot.adapters import LiveWeather
from engine.scorecard import system_stat
from engine.solar import IST
from weather.sources import cross_check

from . import wiring

log = logging.getLogger("suryasach")


def audit_system(store, weather, p, today):
    start, end = today - timedelta(days=40), today - timedelta(days=5)
    rows = store.list_readings(p["phone"], start.isoformat(), end.isoformat())
    todo = [r for r in rows if r.get("ghi_nasa") is None and r.get("ghi")]
    if todo and p.get("lat") is not None:
        nasa = weather.fetch_nasa(p["lat"], p["lon"], start, end)
        for r in todo:
            v = nasa.get(r["date"])
            if v:
                r["ghi_nasa"] = round(v, 3)
                r["ghi_check"] = cross_check(r["ghi"], v)
                store.put_reading(p["phone"], r)
    stat = system_stat(store.list_readings(p["phone"]))
    if stat and p.get("vendor"):
        store.put_vendor_stat(p["vendor"], p["phone"], {**stat, "simulated": bool(p.get("sim"))})
    return len(todo)


def handler(_event, _ctx):
    store, weather = wiring.store(), LiveWeather()
    today = datetime.now(IST).date()
    done = 0
    for p in store.list_profiles():
        if p.get("state") != "READY":
            continue
        try:
            done += audit_system(store, weather, p, today)
        except Exception:  # noqa: BLE001
            log.exception("audit failed for %s", p["phone"][-4:])
    return {"audited_readings": done}
