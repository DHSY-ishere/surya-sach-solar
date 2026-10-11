import hashlib
import hmac
import json

from bot.adapters import extract_messages, verify_signature
from engine.scorecard import aggregate, system_stat
from evidence.pack import content_hash
from handlers.public_api import verify_pack
from store.stores import MemoryStore


def test_signature_roundtrip():
    body = b'{"x":1}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, good)
    assert not verify_signature("secret", body, "sha256=deadbeef")
    assert not verify_signature("secret", body, None)


def test_extract_messages_all_types():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"id": "1", "from": "91", "timestamp": "1", "type": "text", "text": {"body": "hi"}},
        {"id": "2", "from": "91", "timestamp": "2", "type": "image", "image": {"id": "M", "caption": "bill"}},
        {"id": "3", "from": "91", "timestamp": "3", "type": "location",
         "location": {"latitude": 26.9, "longitude": 75.8, "name": "Home"}},
        {"id": "4", "from": "91", "timestamp": "4", "type": "interactive",
         "interactive": {"button_reply": {"title": "yes"}}}]}}]}]}
    m = extract_messages(payload)
    assert [x["type"] for x in m] == ["text", "image", "location", "text"]
    assert m[1]["media_id"] == "M" and m[1]["text"] == "bill"
    assert m[2]["lat"] == 26.9 and m[3]["text"] == "yes"


def test_scorecard_ranks_and_flags_preliminary():
    stats = [{"vendor": "Alpha Solar", "phone": str(i), "median_ratio": r, "n_days": 20}
             for i, r in enumerate([1.0, 0.98, 0.97])]
    stats += [{"vendor": "alpha solar ", "phone": "9", "median_ratio": 0.99, "n_days": 10},
              {"vendor": "BadCo", "phone": "b1", "median_ratio": 0.62, "n_days": 25},
              {"vendor": "BadCo", "phone": "b2", "median_ratio": 0.70, "n_days": 25}]
    out = aggregate(stats)
    assert out[0]["vendor"].lower().startswith("alpha") and out[0]["systems"] == 4
    assert not out[0]["preliminary"]
    bad = out[-1]
    assert bad["vendor"] == "BadCo" and bad["preliminary"] and bad["pct_underperforming"] == 100


def test_system_stat_needs_three_days():
    assert system_stat([{"ratio": 0.9}]) is None
    assert system_stat([{"ratio": 0.9}, {"ratio": 0.8}, {"ratio": 0.7}])["median_ratio"] == 0.8


def test_verify_pack_detects_tampering():
    store = MemoryStore()
    manifest = {"created_at": "t", "owner": "xxxxxx2345", "kwp": 2, "simulated": False,
                "rows": [{"date": "d", "actual_kwh": 5.0, "expected_low": 8, "expected_high": 9,
                          "ratio": 0.6, "photo_sha256": "ab", "ghi_nasa": None}],
                "diagnosis": {"status": "short", "shortfall_pct": 30, "lost_inr_month": 500, "findings": []}}
    pid = content_hash(manifest)
    store.put_pack({"id": pid, "manifest": manifest, "pdf_sha256": "x"})
    status, body = verify_pack(store, pid)
    assert status == 200 and body["intact"]
    manifest["rows"][0]["actual_kwh"] = 8.9            # someone edits the record afterwards
    store.put_pack({"id": pid, "manifest": manifest, "pdf_sha256": "x"})
    assert verify_pack(store, pid)[1]["intact"] is False
    assert verify_pack(store, "nope")[0] == 404


def test_dashboard_token_roundtrip_and_tamper():
    from engine.dashboard import make_token, read_token
    tok = make_token("secret", "919999912345")
    assert read_token("secret", tok) == "919999912345"
    assert read_token("other", tok) is None
    assert read_token("secret", "919999999999." + tok.split(".")[1]) is None
    assert read_token("secret", "") is None


def test_public_systems_list_excludes_real_owners():
    from handlers.public_api import me_payload, systems_payload
    from engine.dashboard import make_token
    store = MemoryStore()
    row = {"date": "2026-10-01", "actual": 5.0, "band_low": 7.0, "band_high": 8.0, "ratio": 0.66, "h_poa": 6.0}
    store.put_profile({"phone": "sim1", "state": "READY", "kwp": 2, "sim": True, "name": "Demo"})
    store.put_profile({"phone": "real1", "state": "READY", "kwp": 2, "name": "Private Person"})
    for ph in ("sim1", "real1"):
        for i in range(1, 5):
            store.put_reading(ph, {**row, "date": f"2026-10-0{i}"})
    ids = [s["id"] for s in systems_payload(store)["systems"]]
    assert ids == ["sim1"]
    assert me_payload(store, "sec", make_token("sec", "real1"))[0] == 200     # owner's own link works
    assert me_payload(store, "sec", "real1.badsig")[0] == 404
