"""Pure-Python clear-sky irradiance (Haurwitz) so we can tell a cloudy day from a dusty panel."""
import math
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def _solar_cos_zenith(lat: float, lon: float, when_utc: datetime) -> float:
    n = when_utc.timetuple().tm_yday
    frac_hour = when_utc.hour + when_utc.minute / 60.0
    g = 2 * math.pi / 365.0 * (n - 1 + (frac_hour - 12) / 24.0)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    true_solar_min = (when_utc.hour * 60 + when_utc.minute + eqtime + 4 * lon) % 1440
    hour_angle = math.radians(true_solar_min / 4 - 180)
    phi = math.radians(lat)
    return (math.sin(phi) * math.sin(decl)
            + math.cos(phi) * math.cos(decl) * math.cos(hour_angle))


def clear_sky_ghi_wm2(lat: float, lon: float, when_utc: datetime) -> float:
    """Haurwitz clear-sky global horizontal irradiance in W/m2."""
    cz = _solar_cos_zenith(lat, lon, when_utc)
    if cz <= 0.01:
        return 0.0
    return 1098.0 * cz * math.exp(-0.059 / cz)


def clear_sky_daily_kwh_m2(lat: float, lon: float, day: date) -> float:
    """Integrate clear-sky GHI over a local (IST) calendar day, 10-minute steps."""
    start = datetime(day.year, day.month, day.day, tzinfo=IST).astimezone(timezone.utc)
    total_wh = 0.0
    for i in range(24 * 6):
        t = start + timedelta(minutes=10 * i + 5)
        total_wh += clear_sky_ghi_wm2(lat, lon, t) * (10 / 60.0)
    return total_wh / 1000.0
