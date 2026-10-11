"""Turn a series of daily readings into a ranked diagnosis."""
from dataclasses import dataclass
from statistics import median

from .model import expected_band

SUNNY_K = 0.80       # clear-sky index above which a day counts as sunny
RAIN_WASH_MM = 5.0


@dataclass
class Day:
    date: str
    actual: float              # kWh from the owner
    h_poa: float               # kWh/m2 plane-of-array
    rain_mm: float = 0.0
    t_amb: float = 30.0
    clear_sky_h: float = 0.0   # clear-sky h_poa; 0 means treat the day as sunny


def ratio(d: Day, kwp: float) -> float:
    b = expected_band(kwp, d.h_poa, d.t_amb)
    mid = (b.low + b.high) / 2
    return d.actual / mid if mid else 0.0


def _is_sunny(d: Day) -> bool:
    return d.clear_sky_h <= 0 or d.h_poa / d.clear_sky_h >= SUNNY_K


def diagnose(days: list[Day], kwp: float, tariff: float = 8.0) -> dict:
    days = sorted(days, key=lambda d: d.date)
    last = days[-1]
    band = expected_band(kwp, last.h_poa, last.t_amb)
    mid = (band.low + band.high) / 2
    shortfall_pct = round((1 - last.actual / mid) * 100, 1)
    status = "short" if last.actual < band.low else "healthy"

    findings = []
    zeros = [d for d in days if d.actual <= 0.05 and _is_sunny(d)]
    if len(zeros) >= 2:
        findings.append(("inverter_tripping", 0.8,
                         f"{len(zeros)} sunny days with almost no output. "
                         "Check the inverter error log and grid voltage."))

    sunny = [d for d in days if _is_sunny(d) and d.actual > 0.05]
    rs = [ratio(d, kwp) for d in sunny]
    if len(rs) >= 3:
        m = median(rs)
        spread = max(rs) - min(rs)
        soil = _soiling(days, kwp)
        if 0.40 <= m <= 0.60 and spread < 0.15:
            findings.append(("dead_string", 0.9,
                             "Output is steady near half of expected on sunny days: one string is "
                             "likely dead. Check the DC isolator, MC4 connectors and string fuse."))
        elif soil:
            findings.append(("soiling", soil["conf"], soil["msg"]))
        elif m < 0.80 and spread < 0.15:
            findings.append(("shading_or_degradation", 0.6,
                             "Consistently low even on sunny days with no dust pattern. Check for new "
                             "shadows (water tank, tree, neighbour's floor) or module degradation."))

    lost_kwh_month = max(0.0, (mid - last.actual) * 30)
    return {
        "date": last.date,
        "actual": last.actual,
        "band": (band.low, band.high),
        "status": status,
        "shortfall_pct": shortfall_pct,
        "lost_inr_month": round(lost_kwh_month * tariff),
        "findings": [{"cause": c, "confidence": cf, "advice": msg}
                     for c, cf, msg in sorted(findings, key=lambda x: -x[1])],
    }


def _soiling(days: list[Day], kwp: float):
    """Soiling: output steps up after a rain-wash day, or drifts down over a dry spell."""
    jumps = []
    for i, d in enumerate(days):
        if d.rain_mm >= RAIN_WASH_MM and i >= 1 and i <= len(days) - 2:
            before = [ratio(x, kwp) for x in days[max(0, i - 3):i] if _is_sunny(x) and x.rain_mm < 1]
            after = [ratio(x, kwp) for x in days[i + 1:i + 4] if _is_sunny(x) and x.rain_mm < 1]
            if before and after:
                jump = (sum(after) / len(after)) / (sum(before) / len(before)) - 1
                jumps.append((jump, d))
    best = max(jumps, key=lambda j: j[0], default=None)
    if best and best[0] >= 0.08:
        return {"conf": 0.85,
                "msg": f"Output jumped {round(best[0] * 100)}% after {best[1].rain_mm:.0f} mm of rain on "
                       f"{best[1].date}. That is dust. Cleaning the panels will recover it."}
    dry = [d for d in days if _is_sunny(d) and d.rain_mm < 1 and d.actual > 0.05]
    if len(dry) >= 6:
        rs = [ratio(d, kwp) for d in dry]
        half = len(rs) // 2
        drift = sum(rs[half:]) / (len(rs) - half) - sum(rs[:half]) / half
        if drift <= -0.06:
            return {"conf": 0.65,
                    "msg": f"Output drifting down {abs(round(drift * 100))}% over a dry spell: "
                           "dust build-up. Clean the panels."}
    return None
