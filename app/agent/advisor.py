"""Free-form questions ("bill zyada kyun aaya?", "should I clean my panels?") answered by a Strands
agent on Amazon Bedrock, with tools that read the owner's real readings. Falls back to a plain
Bedrock call, then to the help text, so the bot never goes silent."""
import json
import logging
import os
from datetime import timedelta

from bot.i18n import t
from engine.diagnose import diagnose
from engine.model import promise_check

log = logging.getLogger("suryasach.agent")
MODEL_ID = os.environ.get("BEDROCK_CHAT_MODEL", "apac.amazon.nova-lite-v1:0")
REGION = os.environ.get("AWS_REGION", "ap-south-1")

SYSTEM = (
    "You are Surya Sach, a WhatsApp assistant that helps Indian rooftop-solar owners find out whether "
    "their system performs as it should. You are an electrical engineer at heart: be concrete, honest "
    "about uncertainty, and brief (under 90 words, plain text, no markdown tables). Always call the "
    "tools to get the owner's real numbers before giving advice; never invent readings. Reply in the "
    "same language the user wrote in (Hindi in Devanagari, or English). Never give legal advice beyond "
    "suggesting they send the evidence pack ('report') to the vendor or discom."
)


def _tools(profile, deps):
    from strands import tool

    @tool
    def get_recent_readings() -> str:
        """Last 14 daily readings: date, actual kWh, healthy band, ratio, rain."""
        rows = deps.store.list_readings(profile["phone"])[-14:]
        return json.dumps([{k: r.get(k) for k in ("date", "actual", "band_low", "band_high", "ratio", "rain_mm")}
                           for r in rows])

    @tool
    def get_diagnosis() -> str:
        """Run the physics diagnosis (dust, dead string, shading, inverter trips) on the last 30 days."""
        from bot.service import Service
        days = Service(deps)._days(profile)
        if not days:
            return "no readings yet"
        dg = diagnose(days, profile["kwp"], profile.get("tariff", 8.0))
        return json.dumps({k: dg[k] for k in ("status", "shortfall_pct", "lost_inr_month", "findings")})

    @tool
    def get_system_profile() -> str:
        """System size, place, vendor, promised units/day, tariff."""
        return json.dumps({k: profile.get(k) for k in ("kwp", "place", "vendor", "promised_kwh", "tariff", "tilt")})

    @tool
    def check_promise(kwp: float, promised_kwh_day: float) -> str:
        """Is a vendor's units/day promise physically possible at the owner's location?"""
        c = deps.weather.site_climate(profile["lat"], profile["lon"])
        return json.dumps(promise_check(kwp, promised_kwh_day, c["annual_psh"], c["best_month_psh"]))

    return [get_recent_readings, get_diagnosis, get_system_profile, check_promise]


def answer(profile, text, deps) -> str:
    lang = profile.get("lang", "en")
    try:
        from strands import Agent
        from strands.models import BedrockModel
        agent = Agent(model=BedrockModel(model_id=MODEL_ID, region_name=REGION, temperature=0.2),
                      system_prompt=SYSTEM, tools=_tools(profile, deps), callback_handler=None)
        reply = str(agent(text)).strip()
        if reply:
            return reply[:1500]
    except Exception:  # noqa: BLE001
        log.exception("strands agent failed")
    return t(lang, "unknown")
