"""Net-meter sanity check: panels made X units, the bill credits only Y."""

# A typical Indian home uses roughly this much on-site during daylight; anything beyond it
# should have been exported and credited on the bill.
MAX_DAYTIME_SELF_USE_KWH_DAY = 6.0


def check_net_meter(generated_kwh: float, exported_kwh: float, days: int) -> dict:
    days = max(days, 1)
    unexplained = generated_kwh - exported_kwh
    per_day = unexplained / days
    suspect = (exported_kwh < 0.15 * generated_kwh) and per_day > MAX_DAYTIME_SELF_USE_KWH_DAY
    return {
        "suspect": suspect,
        "generated_kwh": round(generated_kwh, 1),
        "exported_kwh": round(exported_kwh, 1),
        "unexplained_kwh_per_day": round(per_day, 1),
        "note": ("Your panels made far more than your home could use in daytime, yet the bill "
                 "credits almost nothing. The meter may not be bidirectional or net metering "
                 "may not be configured on your connection.") if suspect else
                "Export credit on your bill looks consistent with what your panels made.",
    }
