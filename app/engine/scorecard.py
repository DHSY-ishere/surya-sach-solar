"""Vendor scorecard: aggregate per-system performance into a per-installer ranking."""
from statistics import median

UNDERPERFORMING_BELOW = 0.90   # system median ratio (actual / mid of healthy band)
MIN_SYSTEMS_FOR_RANK = 3


def system_stat(readings: list[dict]) -> dict | None:
    ratios = [r["ratio"] for r in readings[-30:] if r.get("ratio") is not None]
    if len(ratios) < 3:
        return None
    return {"median_ratio": round(median(ratios), 3), "n_days": len(ratios)}


def aggregate(stats: list[dict]) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for s in stats:
        name = (s.get("vendor") or "").strip()
        if name:
            by.setdefault(name.lower(), []).append(s)
    out = []
    for key, rows in by.items():
        ratios = [r["median_ratio"] for r in rows]
        out.append({
            "vendor": max((r["vendor"] for r in rows), key=len).strip(),
            "systems": len(rows),
            "days": sum(r["n_days"] for r in rows),
            "score": round(median(ratios), 3),
            "pct_underperforming": round(100 * sum(1 for x in ratios if x < UNDERPERFORMING_BELOW) / len(ratios)),
            "preliminary": len(rows) < MIN_SYSTEMS_FOR_RANK,
            "simulated": any(r.get("simulated") for r in rows),
        })
    return sorted(out, key=lambda v: (v["preliminary"], -v["score"]))
