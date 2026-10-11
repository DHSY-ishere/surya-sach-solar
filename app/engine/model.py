"""Physics model: how much should a rooftop PV system make on a given day."""
from dataclasses import dataclass

PR_HEALTHY = 0.80    # upper edge of the healthy band
PR_LOW = 0.72        # lower edge: mild dust, wiring and inverter losses
GAMMA = -0.004       # power temperature coefficient, per degC (crystalline Si)
NOCT_RISE = 25.0     # cell temp rise over ambient at 800 W/m2
BEST_CASE_PR = 0.85  # most generous PR we ever grant a vendor
TILT_GAIN = 1.08     # generous allowance for a tilted plane vs horizontal, annual


@dataclass
class Band:
    low: float
    high: float

    def contains(self, x: float) -> bool:
        return self.low <= x <= self.high


def temp_factor(t_amb_c: float, h_poa_kwh_m2: float) -> float:
    """Derating from cell temperature, using irradiance averaged over ~6 effective hours."""
    g_avg = (h_poa_kwh_m2 * 1000) / 6.0
    t_cell = t_amb_c + NOCT_RISE * min(g_avg / 800.0, 1.3)
    return 1.0 + GAMMA * (t_cell - 25.0)


def expected_band(kwp: float, h_poa: float, t_amb_c: float = 30.0) -> Band:
    """Healthy daily kWh band for a system of kwp at plane-of-array irradiation h_poa."""
    f = temp_factor(t_amb_c, h_poa)
    return Band(round(kwp * h_poa * PR_LOW * f, 2), round(kwp * h_poa * PR_HEALTHY * f, 2))


def promise_check(kwp: float, promised_kwh_day: float, annual_psh: float,
                  best_month_psh: float | None = None) -> dict:
    """Is the vendor's promise physically possible?

    annual_psh: yearly-average peak sun hours (GHI, kWh/m2/day) at the site.
    best_month_psh: best month's average. A daily promise above even the best month's
    ceiling is impossible on every day of the year.
    """
    best = best_month_psh or annual_psh
    avg_ceiling = round(kwp * annual_psh * TILT_GAIN * BEST_CASE_PR, 2)
    # In the sunniest (summer) month the sun is high, so a tilted plane gains nothing over horizontal.
    peak_ceiling = round(kwp * best * BEST_CASE_PR, 2)
    typical = round(kwp * annual_psh * 0.75, 2)
    if promised_kwh_day > peak_ceiling:
        verdict = "impossible"
    elif promised_kwh_day > avg_ceiling:
        verdict = "peak_only"      # only achievable in the sunniest months
    else:
        verdict = "possible"
    return {
        "verdict": verdict,
        "possible": verdict != "impossible",
        "ceiling_kwh_day": avg_ceiling,
        "peak_month_ceiling_kwh_day": peak_ceiling,
        "typical_kwh_day": typical,
        "overpromise_pct": round((promised_kwh_day / avg_ceiling - 1) * 100, 1)
        if promised_kwh_day > avg_ceiling else 0.0,
        "required_psh": round(promised_kwh_day / (kwp * TILT_GAIN * BEST_CASE_PR), 2),
    }
