"""WhatsApp webhook: verify handshake, check signature, enqueue (FIFO per user) and ack fast."""
import base64
import json
import os

import boto3

from bot.adapters import extract_messages, verify_signature

_sqs = boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "ap-south-1"))


def handler(event, _ctx):
    method = event["requestContext"]["http"]["method"]
    if method == "GET":                      # Meta's one-time verification
        q = event.get("queryStringParameters") or {}
        if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == os.environ["WA_VERIFY_TOKEN"]:
            return {"statusCode": 200, "body": q.get("hub.challenge", "")}
        return {"statusCode": 403, "body": "forbidden"}

    raw = event.get("body") or ""
    body = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode()
    sig = (event.get("headers") or {}).get("x-hub-signature-256")
    secret = os.environ.get("WA_APP_SECRET", "")
    # Signature check switches on as soon as the Meta app secret is configured.
    if secret and secret != "pending-setup" and not verify_signature(secret, body, sig):
        return {"statusCode": 401, "body": "bad signature"}

    msgs = extract_messages(json.loads(body))
    for i in range(0, len(msgs), 10):
        _sqs.send_message_batch(QueueUrl=os.environ["QUEUE_URL"], Entries=[
            {"Id": str(j), "MessageBody": json.dumps(m), "MessageGroupId": m["from"],
             "MessageDeduplicationId": m["id"]} for j, m in enumerate(msgs[i:i + 10])])
    return {"statusCode": 200, "body": "ok"}
