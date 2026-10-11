"""Storage: one interface, an in-memory implementation (local runs, tests) and DynamoDB (AWS)."""
import json
from decimal import Decimal


class MemoryStore:
    def __init__(self):
        self.profiles: dict[str, dict] = {}
        self.readings: dict[str, dict[str, dict]] = {}
        self.seen: set[str] = set()
        self.packs: dict[str, dict] = {}
        self.vendor_stats: dict[tuple[str, str], dict] = {}

    def get_profile(self, phone):
        return self.profiles.get(phone)

    def put_profile(self, profile):
        self.profiles[profile["phone"]] = json.loads(json.dumps(profile))

    def list_profiles(self):
        return list(self.profiles.values())

    def put_reading(self, phone, reading):
        self.readings.setdefault(phone, {})[reading["date"]] = dict(reading)

    def get_reading(self, phone, day):
        return self.readings.get(phone, {}).get(day)

    def list_readings(self, phone, start=None, end=None):
        rows = sorted(self.readings.get(phone, {}).values(), key=lambda r: r["date"])
        return [r for r in rows if (not start or r["date"] >= start) and (not end or r["date"] <= end)]

    def mark_seen(self, msg_id):
        """True if first time we see this id."""
        if msg_id in self.seen:
            return False
        self.seen.add(msg_id)
        return True

    def put_pack(self, pack):
        self.packs[pack["id"]] = dict(pack)

    def get_pack(self, pack_id):
        return self.packs.get(pack_id)

    def put_vendor_stat(self, vendor, phone, stat):
        self.vendor_stats[(vendor, phone)] = dict(stat)

    def list_vendor_stats(self):
        return [{"vendor": v, "phone": p, **s} for (v, p), s in self.vendor_stats.items()]


def _to_dynamo(o):
    return json.loads(json.dumps(o), parse_float=Decimal)


def _from_dynamo(o):
    if isinstance(o, list):
        return [_from_dynamo(x) for x in o]
    if isinstance(o, dict):
        return {k: _from_dynamo(v) for k, v in o.items()}
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    return o


class DynamoStore:
    """Single-table design: PK/SK strings.

    SYS#<phone> | PROFILE        profile
    SYS#<phone> | READ#<date>    daily reading
    MSG#<id>    | SEEN           idempotency marker (TTL)
    PACK#<id>   | META           evidence pack metadata (public verify)
    VENDOR#<v>  | SYS#<phone>    per-system stats for the scorecard
    """

    def __init__(self, table):
        self.t = table

    def _get(self, pk, sk):
        item = self.t.get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return _from_dynamo(item) if item else None

    def _put(self, item, **kw):
        self.t.put_item(Item=_to_dynamo(item), **kw)

    def _query(self, pk, sk_prefix=None, sk_between=None):
        from boto3.dynamodb.conditions import Key
        cond = Key("pk").eq(pk)
        if sk_between:
            cond = cond & Key("sk").between(*sk_between)
        elif sk_prefix:
            cond = cond & Key("sk").begins_with(sk_prefix)
        items, kwargs = [], {"KeyConditionExpression": cond}
        while True:
            res = self.t.query(**kwargs)
            items += res.get("Items", [])
            if "LastEvaluatedKey" not in res:
                return [_from_dynamo(i) for i in items]
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]

    def get_profile(self, phone):
        item = self._get(f"SYS#{phone}", "PROFILE")
        return item["data"] if item else None

    def put_profile(self, profile):
        self._put({"pk": f"SYS#{profile['phone']}", "sk": "PROFILE", "type": "PROFILE", "data": profile})

    def list_profiles(self):
        from boto3.dynamodb.conditions import Attr
        items, kwargs = [], {"FilterExpression": Attr("type").eq("PROFILE")}
        while True:
            res = self.t.scan(**kwargs)
            items += res.get("Items", [])
            if "LastEvaluatedKey" not in res:
                return [_from_dynamo(i)["data"] for i in items]
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]

    def put_reading(self, phone, reading):
        self._put({"pk": f"SYS#{phone}", "sk": f"READ#{reading['date']}", "data": reading})

    def get_reading(self, phone, day):
        item = self._get(f"SYS#{phone}", f"READ#{day}")
        return item["data"] if item else None

    def list_readings(self, phone, start=None, end=None):
        lo, hi = f"READ#{start or '0000-00-00'}", f"READ#{end or '9999-99-99'}"
        return [i["data"] for i in self._query(f"SYS#{phone}", sk_between=(lo, hi))]

    def mark_seen(self, msg_id):
        import time
        from botocore.exceptions import ClientError
        try:
            self._put({"pk": f"MSG#{msg_id}", "sk": "SEEN", "ttl": int(time.time()) + 3 * 86400},
                      ConditionExpression="attribute_not_exists(pk)")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def put_pack(self, pack):
        self._put({"pk": f"PACK#{pack['id']}", "sk": "META", "data": pack})

    def get_pack(self, pack_id):
        item = self._get(f"PACK#{pack_id}", "META")
        return item["data"] if item else None

    def put_vendor_stat(self, vendor, phone, stat):
        self._put({"pk": f"VENDOR#{vendor.lower()}", "sk": f"SYS#{phone}", "type": "VSTAT",
                   "data": {"vendor": vendor, "phone": phone, **stat}})

    def list_vendor_stats(self):
        from boto3.dynamodb.conditions import Attr
        items, kwargs = [], {"FilterExpression": Attr("type").eq("VSTAT")}
        while True:
            res = self.t.scan(**kwargs)
            items += res.get("Items", [])
            if "LastEvaluatedKey" not in res:
                return [_from_dynamo(i)["data"] for i in items]
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]
