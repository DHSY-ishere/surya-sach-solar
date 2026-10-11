"""19:00 IST daily: ask owners for today's reading. Free-form text inside WhatsApp's 24h window,
an approved template outside it."""
import logging
import time

from bot.i18n import t

from . import wiring

log = logging.getLogger("suryasach")
WINDOW_S = 23 * 3600


def handler(_event, _ctx):
    store, wa = wiring.store(), wiring.whatsapp()
    from datetime import datetime
    from engine.solar import IST
    today = datetime.now(IST).date().isoformat()
    sent = skipped = 0
    for p in store.list_profiles():
        if p.get("state") != "READY" or p.get("sim") or p.get("nudges") is False:
            continue
        if store.get_reading(p["phone"], today):
            skipped += 1
            continue
        try:
            if time.time() - (p.get("last_seen") or 0) < WINDOW_S:
                wa.send_text(p["phone"], t(p.get("lang", "en"), "nudge"))
            else:
                wa.send_template(p["phone"])          # opens a new 24h window
            sent += 1
        except Exception:  # noqa: BLE001
            log.exception("nudge failed for %s", p["phone"][-4:])
    return {"sent": sent, "skipped": skipped}
