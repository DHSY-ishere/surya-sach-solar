import base64
import hashlib
import hmac
import importlib
import json

import boto3
import pytest
from moto import mock_aws

from store.stores import DynamoStore


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
        monkeypatch.setenv("AWS_REGION", "ap-south-1")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
        ddb = boto3.resource("dynamodb", region_name="ap-south-1")
        table = ddb.create_table(
            TableName="t", BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}])
        yield table


def test_dynamo_store_roundtrip(aws):
    s = DynamoStore(aws)
    s.put_profile({"phone": "91", "kwp": 2.5, "state": "READY", "pending": None, "promise_check": {"x": 1.5}})
    assert s.get_profile("91")["kwp"] == 2.5 and s.get_profile("91")["promise_check"]["x"] == 1.5
    assert [p["phone"] for p in s.list_profiles()] == ["91"]
    for d in ("2026-10-01", "2026-10-02", "2026-10-03"):
        s.put_reading("91", {"date": d, "actual": 5.4, "ratio": 0.7})
    assert len(s.list_readings("91")) == 3
    assert [r["date"] for r in s.list_readings("91", "2026-10-02", "2026-10-03")] == ["2026-10-02", "2026-10-03"]
    assert s.get_reading("91", "2026-10-01")["actual"] == 5.4
    assert s.mark_seen("wamid.1") is True and s.mark_seen("wamid.1") is False
    s.put_pack({"id": "abc", "manifest": {"k": 1.25}})
    assert s.get_pack("abc")["manifest"]["k"] == 1.25 and s.get_pack("zzz") is None
    s.put_vendor_stat("SunnyRoofs", "91", {"median_ratio": 0.6, "n_days": 20})
    assert s.list_vendor_stats()[0]["median_ratio"] == 0.6


def test_ingest_handler_verifies_and_enqueues(aws, monkeypatch):
    sqs = boto3.client("sqs", region_name="ap-south-1")
    url = sqs.create_queue(QueueName="q.fifo", Attributes={"FifoQueue": "true"})["QueueUrl"]
    monkeypatch.setenv("QUEUE_URL", url)
    monkeypatch.setenv("WA_VERIFY_TOKEN", "vt")
    monkeypatch.setenv("WA_APP_SECRET", "secret")
    import handlers.ingest as ingest
    importlib.reload(ingest)

    get = {"requestContext": {"http": {"method": "GET"}},
           "queryStringParameters": {"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"}}
    assert ingest.handler(get, None) == {"statusCode": 200, "body": "42"}
    get["queryStringParameters"]["hub.verify_token"] = "wrong"
    assert ingest.handler(get, None)["statusCode"] == 403

    payload = json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.9", "from": "91", "timestamp": "1", "type": "text", "text": {"body": "hi"}}]}}]}]}).encode()
    sig = "sha256=" + hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
    post = {"requestContext": {"http": {"method": "POST"}}, "body": payload.decode(),
            "headers": {"x-hub-signature-256": sig}}
    assert ingest.handler(post, None)["statusCode"] == 200
    msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=1)["Messages"]
    assert json.loads(msgs[0]["Body"])["text"] == "hi"
    post["headers"]["x-hub-signature-256"] = "sha256=bad"
    assert ingest.handler(post, None)["statusCode"] == 401


def test_audit_backfills_nasa_and_writes_vendor_stats(aws):
    from datetime import date
    from handlers.audit import audit_system
    s = DynamoStore(aws)
    prof = {"phone": "91", "lat": 26.9, "lon": 75.8, "vendor": "V", "state": "READY"}
    for i in range(1, 10):
        s.put_reading("91", {"date": f"2026-09-{i:02d}", "actual": 5.0, "ghi": 5.6, "ratio": 0.6})

    class W:
        def fetch_nasa(self, *a):
            return {f"2026-09-{i:02d}": 5.5 for i in range(1, 10)}

    n = audit_system(s, W(), prof, date(2026, 10, 10))
    assert n == 9
    r = s.get_reading("91", "2026-09-03")
    assert r["ghi_nasa"] == 5.5 and r["ghi_check"]["agree"] is True
    assert s.list_vendor_stats()[0]["n_days"] == 9
