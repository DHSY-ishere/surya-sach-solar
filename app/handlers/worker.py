"""SQS consumer: runs the conversation. Partial batch failure so one bad message doesn't replay the rest."""
import json
import logging

from . import wiring

log = logging.getLogger("suryasach")
log.setLevel(logging.INFO)


def handler(event, _ctx):
    svc = wiring.service()
    failures = []
    for rec in event["Records"]:
        try:
            svc.process(json.loads(rec["body"]))
        except Exception:  # noqa: BLE001
            log.exception("worker failed on %s", rec["messageId"])
            failures.append({"itemIdentifier": rec["messageId"]})
    return {"batchItemFailures": failures}
