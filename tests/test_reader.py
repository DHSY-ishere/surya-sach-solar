from reader.ocr import read_photo
from reader.parse import parse_energy


def test_parse_today_kwh():
    r = parse_energy("Growatt\nToday 5.4 kWh\nTotal 1234.5 kWh\nPower 0.8 kW")
    assert r["kind"] == "daily" and r["value"] == 5.4 and r["confidence"] >= 0.8


def test_parse_total_only():
    r = parse_energy("E-Total\n1234.5 kWh")
    assert r["kind"] == "total" and r["value"] == 1234.5


def test_parse_comma_decimal():
    assert parse_energy("Daily yield 7,2 kWh")["value"] == 7.2


def test_parse_nothing():
    assert parse_energy("Hello world") is None


class FakeTextract:
    def __init__(self, lines):
        self.lines = lines

    def detect_document_text(self, Document):
        return {"Blocks": [{"BlockType": "LINE", "Text": t} for t in self.lines]}


class FakeBedrock:
    def __init__(self, text):
        self.text = text

    def converse(self, **kw):
        return {"output": {"message": {"content": [{"text": self.text}]}}}


def test_textract_used_when_confident():
    r = read_photo(b"x", FakeTextract(["Today 5.4 kWh"]), FakeBedrock("{}"))
    assert r["engine"] == "textract" and r["value"] == 5.4


def test_vision_fallback_when_textract_blank():
    r = read_photo(b"x", FakeTextract([]), FakeBedrock('ok {"kind":"daily","value":6.1}'))
    assert r["engine"] == "bedrock-vision" and r["value"] == 6.1


def test_unreadable():
    r = read_photo(b"x", FakeTextract([]), FakeBedrock("sorry"))
    assert r["kind"] == "unknown"


def test_bill_goes_to_vision():
    r = read_photo(b"x", FakeTextract(["Today 5 kWh"]),
                   FakeBedrock('{"kind":"bill","value":null,"export_units":12,"import_units":300}'),
                   want_bill=True)
    assert r["kind"] == "bill" and r["export_units"] == 12
