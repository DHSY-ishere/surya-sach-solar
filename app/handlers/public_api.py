"""Public, read-only API behind CloudFront: vendor scorecard, pack verification, promise check."""
import os

from bot.adapters import LiveWeather
from engine.dashboard import dashboard_payload, read_token
from engine.model import promise_check
from engine.scorecard import aggregate
from evidence.pack import content_hash

from . import wiring


def verify_pack(store, pack_id: str, presign=None) -> tuple[int, dict]:
    pack = store.get_pack(pack_id)
    if not pack:
        return 404, {"found": False}
    recomputed = content_hash(pack["manifest"])
    m = pack["manifest"]
    rows = m["rows"]
    body = {
        "found": True, "id": pack_id, "intact": recomputed == pack_id,
        "recomputed": recomputed, "pdf_sha256": pack.get("pdf_sha256"),
        "created_at": m["created_at"], "owner": m["owner"], "place": m.get("place"),
        "kwp": m["kwp"], "vendor": m.get("vendor"), "promised_kwh_day": m.get("promised_kwh_day"),
        "days": len(rows), "simulated": m["simulated"],
        "photos": sum(1 for r in rows if r.get("photo_sha256")),
        "nasa_checked": sum(1 for r in rows if r.get("ghi_nasa")),
        "diagnosis": m["diagnosis"], "promise_check": m.get("promise_check"),
        "rows": [{k: r[k] for k in ("date", "actual_kwh", "expected_low", "expected_high", "ratio")} for r in rows],
    }
    if presign and pack.get("pdf_key"):
        body["pdf_url"] = presign(pack["pdf_key"])
    return 200, body


def systems_payload(store) -> dict:
    """Public list: demonstration systems only. Real owners are reachable only via their private token."""
    return {"systems": [dashboard_payload(p, store.list_readings(p["phone"]))
                        for p in store.list_profiles() if p.get("sim") and p.get("state") == "READY"]}


def me_payload(store, secret: str, token: str):
    phone = read_token(secret, token)
    p = store.get_profile(phone) if phone else None
    if not p or p.get("state") != "READY":
        return 404, {"error": "unknown or invalid link"}
    return 200, dashboard_payload(p, store.list_readings(phone))


def handler(event, _ctx):
    path = event.get("rawPath", "")
    q = event.get("queryStringParameters") or {}
    store = wiring.store()
    if path.endswith("/systems"):
        return wiring.json_response(200, systems_payload(store))
    if path.endswith("/me"):
        status, body = me_payload(store, wiring.dashboard_secret(), q.get("t", ""))
        return wiring.json_response(status, body)
    if path.endswith("/vendors"):
        return wiring.json_response(200, {"vendors": aggregate(store.list_vendor_stats())})
    if path.endswith("/verify"):
        import boto3
        s3 = boto3.client("s3")
        status, body = verify_pack(store, q.get("id", ""), lambda k: s3.generate_presigned_url(
            "get_object", Params={"Bucket": os.environ["BUCKET"], "Key": k}, ExpiresIn=3600))
        return wiring.json_response(status, body)
    if path.endswith("/promise"):
        try:
            kwp, promised = float(q["kwp"]), float(q["promised"])
            lat, lon = float(q.get("lat", 26.91)), float(q.get("lon", 75.79))
        except (KeyError, ValueError):
            return wiring.json_response(400, {"error": "need kwp, promised (and optionally lat, lon)"})
        clim = LiveWeather().site_climate(lat, lon)
        return wiring.json_response(200, {**promise_check(kwp, promised, clim["annual_psh"],
                                                          clim["best_month_psh"]), "climate": clim})
    return wiring.json_response(404, {"error": "not found"})
