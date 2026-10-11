"""Pull an energy figure out of OCR text from an inverter / meter screen."""
import re

NUM = r"(\d{1,6}(?:[.,]\d{1,2})?)"
DAILY_WORDS = ("today", "daily", "day", "e-day", "eday", "e_day", "आज")
TOTAL_WORDS = ("total", "lifetime", "e-total", "etotal", "e_total", "cumulative", "yield total")
UNIT = re.compile(NUM + r"\s*(kwh|kw\.h|units|unit|यूनिट)\b", re.I)
LOOSE = re.compile(NUM)


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def parse_energy(text: str) -> dict | None:
    """Return {'value', 'kind': 'daily'|'total', 'confidence'} or None.

    Strategy: score each number by its unit, and by 'today'/'total' words on its line or the line above.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    cands = []
    for i, line in enumerate(lines):
        window = (lines[i - 1] + " " if i else "") + line
        low = window.lower()
        is_total = any(w in low for w in TOTAL_WORDS)
        is_daily = any(w in low for w in DAILY_WORDS) and not is_total
        for m in UNIT.finditer(line):
            v = _f(m.group(1))
            conf = 0.55 + (0.3 if is_daily or is_total else 0.0)
            cands.append((conf, v, "total" if is_total else "daily"))
        if not UNIT.search(line):
            nums = LOOSE.findall(line)
            if len(nums) == 1 and (is_daily or is_total):
                cands.append((0.45, _f(nums[0]), "total" if is_total else "daily"))
    if not cands:
        return None
    # prefer a labelled 'today' reading; otherwise the best-scored
    cands.sort(key=lambda c: (c[2] == "daily", c[0]), reverse=True)
    conf, v, kind = cands[0]
    return {"value": v, "kind": kind, "confidence": round(conf, 2)}
