"""Builds the real dependency graph from environment variables (cached across warm Lambda invocations)."""
import os
from functools import lru_cache

import boto3

from bot.adapters import LiveWeather, S3Blob, WhatsApp, polly_tts
from bot.service import Deps, Service
from engine.dashboard import make_token
from reader.ocr import read_photo
from store.stores import DynamoStore

REGION = os.environ.get("AWS_REGION", "ap-south-1")


@lru_cache(maxsize=1)
def store():
    return DynamoStore(boto3.resource("dynamodb", region_name=REGION).Table(os.environ["TABLE_NAME"]))


@lru_cache(maxsize=1)
def blob():
    return S3Blob(boto3.client("s3", region_name=REGION), os.environ["BUCKET"])


@lru_cache(maxsize=1)
def whatsapp():
    return WhatsApp(os.environ["WA_TOKEN"], os.environ["WA_PHONE_ID"])


@lru_cache(maxsize=1)
def service() -> Service:
    textract = boto3.client("textract", region_name=REGION)
    bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    polly = boto3.client("polly", region_name=REGION)
    advisor = None
    if os.environ.get("ENABLE_AGENT", "1") == "1":
        from agent.advisor import answer as advisor
    return Service(Deps(
        store=store(), msgr=whatsapp(), weather=LiveWeather(),
        read_photo=lambda img, bill: read_photo(img, textract, bedrock, want_bill=bill),
        blob=blob(), verify_base_url=os.environ.get("PUBLIC_BASE_URL", ""),
        tts=polly_tts(polly), advisor=advisor,
        dashboard_token=lambda phone: make_token(dashboard_secret(), phone)))


def dashboard_secret() -> str:
    return os.environ.get("DASH_SECRET") or os.environ["WA_APP_SECRET"]


def json_response(status: int, body, headers: dict | None = None) -> dict:
    import json
    return {"statusCode": status, "headers": {"Content-Type": "application/json", **(headers or {})},
            "body": json.dumps(body)}
