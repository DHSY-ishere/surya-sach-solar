from datetime import date

from engine.diagnose import Day, diagnose
from engine.model import expected_band, promise_check
from engine.netmeter import check_net_meter
from engine.solar import clear_sky_daily_kwh_m2


def mk(vals, rain=None, h=5.6):
    rain = rain or {}
    return [Day(f"2026-10-{i + 1:02d}", v, h, rain.get(i, 0.0), 32) for i, v in enumerate(vals)]


def test_flagship_mis_selling_is_impossible_even_in_best_month():
    # Jaipur-like: annual 5.5, best month 7.0 kWh/m2/day
    r = promise_check(kwp=2, promised_kwh_day=13, annual_psh=5.5, best_month_psh=7.0)
    assert r["verdict"] == "impossible" and not r["possible"]
    assert r["ceiling_kwh_day"] < 11
    assert r["overpromise_pct"] > 15


def test_peak_only_promise():
    r = promise_check(2, 11.5, 5.5, 7.0)
    assert r["verdict"] == "peak_only"


def test_honest_promise_is_possible():
    assert promise_check(2, 8.5, 5.5, 7.0)["verdict"] == "possible"


def test_band_realistic_for_2kw_jaipur():
    b = expected_band(2, 5.6, 32)
    assert 6.5 < b.low < b.high < 9.5


def test_clear_sky_jaipur_october_is_sane():
    kwh = clear_sky_daily_kwh_m2(26.91, 75.79, date(2026, 10, 5))
    assert 5.5 < kwh < 7.5


def test_healthy_system():
    r = diagnose(mk([8.0] * 7), kwp=2)
    assert r["status"] == "healthy" and not r["findings"]


def test_dead_string():
    r = diagnose(mk([4.2, 4.1, 4.3, 4.2, 4.0, 4.2]), kwp=2)
    assert r["findings"][0]["cause"] == "dead_string"


def test_soiling_with_rain_wash():
    vals = [6.3, 6.0, 5.8, 5.6, 8.2, 8.1, 8.0]
    r = diagnose(mk(vals, rain={3: 12.0}), kwp=2)
    assert any(f["cause"] == "soiling" for f in r["findings"])


def test_short_reports_money_lost():
    r = diagnose(mk([5.4] * 5), kwp=2)
    assert r["status"] == "short" and r["shortfall_pct"] > 25 and r["lost_inr_month"] > 0


def test_inverter_tripping():
    r = diagnose(mk([8.0, 0, 8.0, 0, 8.1]), kwp=2)
    assert any(f["cause"] == "inverter_tripping" for f in r["findings"])


def test_net_meter_flags_zero_export():
    assert check_net_meter(generated_kwh=240, exported_kwh=0, days=30)["suspect"]


def test_net_meter_ok_when_exporting():
    assert not check_net_meter(240, 120, 30)["suspect"]
