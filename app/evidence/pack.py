"""Tamper-evident evidence pack: 30 days of expected vs actual, hashes, methodology, QR verify."""
import hashlib
import io
import json
from datetime import datetime, timezone

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

INK = colors.HexColor("#1b1a17")
SUN = colors.HexColor("#d9480f")
MUTE = colors.HexColor("#6b675e")
PAPER = colors.HexColor("#f6f1e7")


def canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(data: dict) -> str:
    return hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_manifest(profile: dict, readings: list[dict], diagnosis: dict, promise: dict | None,
                   created_at: str | None = None) -> dict:
    """Everything the pack asserts, in one canonical dict. Its hash is the pack ID."""
    rows = [{
        "date": r["date"], "actual_kwh": r["actual"],
        "expected_low": r.get("band_low"), "expected_high": r.get("band_high"),
        "ratio": r.get("ratio"), "ghi_open_meteo": r.get("ghi"), "ghi_nasa": r.get("ghi_nasa"),
        "rain_mm": r.get("rain_mm"), "photo_sha256": r.get("photo_sha256"),
        "source": r.get("source"), "simulated": bool(r.get("simulated")),
    } for r in readings]
    return {
        "version": 1,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "owner": "xxxxxx" + str(profile["phone"])[-4:],
        "place": profile.get("place"),
        "lat": profile.get("lat"), "lon": profile.get("lon"),
        "kwp": profile["kwp"], "tilt": profile.get("tilt"), "azimuth": profile.get("azimuth", 180),
        "vendor": profile.get("vendor"), "promised_kwh_day": profile.get("promised_kwh"),
        "tariff": profile.get("tariff"),
        "rows": rows,
        "diagnosis": {k: diagnosis[k] for k in ("status", "shortfall_pct", "lost_inr_month", "findings")},
        "promise_check": promise,
        "simulated": any(r["simulated"] for r in rows),
    }


def _chart(rows: list[dict]) -> Drawing:
    d = Drawing(170 * mm, 62 * mm)
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 28, 22, 170 * mm - 40, 62 * mm - 38
    act = [(i, r["actual_kwh"]) for i, r in enumerate(rows)]
    lo = [(i, r["expected_low"]) for i, r in enumerate(rows) if r["expected_low"] is not None]
    hi = [(i, r["expected_high"]) for i, r in enumerate(rows) if r["expected_high"] is not None]
    lp.data = [act, lo, hi]
    lp.lines[0].strokeColor, lp.lines[0].strokeWidth = SUN, 2
    for i in (1, 2):
        lp.lines[i].strokeColor, lp.lines[i].strokeWidth = MUTE, 0.8
        lp.lines[i].strokeDashArray = (3, 2)
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax = 0, max(len(rows) - 1, 1)
    lp.xValueAxis.labels.fontSize = lp.yValueAxis.labels.fontSize = 7
    lp.yValueAxis.valueMin = 0
    top = max(v for series in (act, lo, hi) for _, v in series) if act else 1
    lp.yValueAxis.valueMax = float(int(top) + 2)
    lp.yValueAxis.valueStep = 2
    d.add(lp)
    d.add(String(28, 62 * mm - 10, "Actual (orange) vs healthy band (dashed), kWh/day",
                 fontSize=8, fillColor=MUTE))
    return d


def render_pdf(manifest: dict, photos_note: str, verify_url: str, pack_id: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Surya Sach evidence pack", author="Surya Sach")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=20,
                        textColor=INK, alignment=0, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12,
                        textColor=SUN, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName="Helvetica", fontSize=9.5,
                          leading=13, textColor=INK)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=10, textColor=MUTE)
    m = manifest
    dg = m["diagnosis"]
    rows = m["rows"]
    story = [Paragraph("Rooftop solar performance evidence", h1),
             Paragraph(f"System {m['kwp']} kWp, {m.get('place') or 'location on file'}. Owner {m['owner']}. "
                       f"Vendor: {m.get('vendor') or 'not stated'}. Issued {m['created_at']}.", body)]
    if m["simulated"]:
        story.append(Paragraph("<b>DEMONSTRATION DATA: readings in this pack are simulated.</b>", body))
    story += [Spacer(1, 6), Paragraph("Summary", h2)]
    avg_actual = sum(r["actual_kwh"] for r in rows) / len(rows)
    mids = [(r["expected_low"] + r["expected_high"]) / 2 for r in rows if r["expected_low"] is not None]
    avg_expected = sum(mids) / len(mids) if mids else 0
    short = (1 - avg_actual / avg_expected) * 100 if avg_expected else 0
    story.append(Paragraph(
        f"Over {len(rows)} recorded days the system averaged <b>{avg_actual:.1f} kWh/day</b> against a healthy "
        f"expectation of <b>{avg_expected:.1f} kWh/day</b> (<b>{short:.0f}% "
        f"{'below' if short > 0 else 'above'}</b>). Estimated loss at the latest rate: "
        f"<b>Rs {dg['lost_inr_month']:,}/month</b>.", body))
    for f in dg["findings"]:
        story.append(Paragraph(f"- <b>{f['cause'].replace('_', ' ').title()}</b> "
                               f"(confidence {int(f['confidence'] * 100)}%): {f['advice']}", body))
    pc = m.get("promise_check")
    if pc and m.get("promised_kwh_day"):
        story.append(Paragraph("What was promised", h2))
        story.append(Paragraph(
            f"Vendor promised {m['promised_kwh_day']} kWh/day from {m['kwp']} kWp. At this site the "
            f"physical ceiling is {pc['ceiling_kwh_day']} kWh/day on average and "
            f"{pc['peak_month_ceiling_kwh_day']} kWh/day in the best month. Verdict: "
            f"<b>{pc['verdict'].replace('_', ' ')}</b>.", body))
    story += [Spacer(1, 4), _chart(rows), Paragraph("Daily record", h2)]
    data = [["Date", "Actual", "Healthy band", "Ratio", "GHI OM", "GHI NASA", "Rain"]]
    for r in rows:
        band = f"{r['expected_low']:.1f}-{r['expected_high']:.1f}" if r["expected_low"] is not None else "-"
        data.append([r["date"], f"{r['actual_kwh']:.1f}", band,
                     f"{r['ratio']:.2f}" if r["ratio"] is not None else "-",
                     f"{r['ghi_open_meteo']:.2f}" if r["ghi_open_meteo"] else "-",
                     f"{r['ghi_nasa']:.2f}" if r["ghi_nasa"] else "pending",
                     f"{r['rain_mm']:.0f}" if r["rain_mm"] is not None else "-"])
    t = Table(data, repeatRows=1, colWidths=[24 * mm, 20 * mm, 30 * mm, 18 * mm, 24 * mm, 26 * mm, 16 * mm])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7.5), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), PAPER), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9d3c4")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [t, PageBreak(), Paragraph("Method", h2), Paragraph(
        "Expected energy per day = system size (kWp) x plane-of-array irradiation (kWh/m2, at the roof's "
        "tilt and azimuth) x performance ratio x temperature factor. The healthy band uses a performance "
        "ratio of 0.72 to 0.80 and a cell-temperature derating of -0.4%/degC. Irradiation comes from "
        "Open-Meteo (same-day) and is cross-checked against NASA POWER (available after about 5 days); "
        "where both are present they are shown side by side. Rain on a day is from the same weather "
        "source. A sunny day is one with clear-sky index above 0.8 (Haurwitz clear-sky model).", body),
        Paragraph("Integrity", h2),
        Paragraph(f"Pack ID (SHA-256 of the canonical record): <font face='Courier' size=7>{pack_id}</font>. "
                  f"{photos_note} Anyone can confirm this pack is unaltered at the address below or by "
                  "scanning the QR code.", body),
        Paragraph(verify_url, small), Spacer(1, 6)]
    qr_w = qr.QrCodeWidget(verify_url)
    b = qr_w.getBounds()
    size = 32 * mm
    qd = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    qd.add(qr_w)
    story += [qd, Paragraph("Suggested next step", h2), Paragraph(_letter(m), body)]
    doc.build(story)
    return buf.getvalue()


def _letter(m: dict) -> str:
    pc = m.get("promise_check")
    parts = [f"To {m.get('vendor') or 'the installer'}: my {m['kwp']} kWp rooftop system is performing "
             f"below the physical expectation for this location, as documented in this pack."]
    if pc and pc["verdict"] == "impossible":
        parts.append(f"The promised {m['promised_kwh_day']} kWh/day exceeds what {m['kwp']} kWp can "
                     f"produce here even in the best month ({pc['peak_month_ceiling_kwh_day']} kWh/day).")
    parts.append("I request an inspection and written explanation within 15 days, failing which I will "
                 "approach the discom and the District Consumer Disputes Redressal Commission.")
    return " ".join(parts)


def make_pack(profile, readings, diagnosis, promise, base_verify_url, created_at=None):
    """Return (pack_id, manifest, pdf_bytes)."""
    manifest = build_manifest(profile, readings, diagnosis, promise, created_at)
    pack_id = content_hash(manifest)
    n_photos = sum(1 for r in manifest["rows"] if r["photo_sha256"])
    note = f"{n_photos} of {len(manifest['rows'])} readings are backed by a stored photo with its own SHA-256."
    url = f"{base_verify_url.rstrip('/')}/verify.html?id={pack_id}"
    return pack_id, manifest, render_pdf(manifest, note, url, pack_id)
