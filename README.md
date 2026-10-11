# Surya Sach: your roof's honest meter

Environmental Hacks (Bharat Builds Tour) · Waste and Energy track · Rooftop solar

Owners message a **photo of their inverter** on WhatsApp. Surya Sach reads the units, works out what the
roof *should* have made that day from real sunlight data, diagnoses the cause of any shortfall, and produces a
**tamper-evident evidence pack** the owner can take to the vendor, the discom, or a consumer court.
A public **vendor scorecard** turns individual complaints into market-level verification.
The website and the bot are linked both ways: every page has a *Chat on WhatsApp* button, and typing `dashboard` in the chat returns a private link to that owner's charts.

## What it does

| Feature | Where |
|---|---|
| Promise Check: is "13 units from 2 kW" physically possible at this site? | `app/engine/model.py`, `web/` (hero), WhatsApp `promise 2 13` |
| Photo → units (Textract, Bedrock-vision fallback, owner confirms) | `app/reader/` |
| Expected-output band (irradiance on the roof's tilt, performance ratio, heat derating) | `app/engine/model.py`, `app/weather/sources.py` |
| Diagnosis: dust (rain-wash natural experiment), dead string, shading, inverter trips | `app/engine/diagnose.py` |
| Net-meter check from a bill photo or `export 12 days 30` | `app/engine/netmeter.py` |
| Two independent weather sources: Open-Meteo (fresh) audited by NASA POWER (5-day lag) | `app/handlers/audit.py` |
| Evidence PDF with photo hashes, QR code, `/verify` page that detects tampering | `app/evidence/`, `web/verify.html` |
| Vendor scorecard | `app/engine/scorecard.py`, `web/index.html` |
| Owner dashboard (chart vs healthy band, rain marks, diagnosis, promise ruler); owners get a private link by typing `dashboard` | `app/engine/dashboard.py`, `web/dashboard.html` |
| Hindi + English, optional voice-note replies (Polly), free-form Q&A (Strands agent on Bedrock) | `app/bot/i18n.py`, `app/agent/` |
| 7 pm nudges, nightly audit (EventBridge Scheduler) | `app/handlers/nudge.py`, `audit.py` |

## Architecture

```
WhatsApp ─► API Gateway ─► Lambda ingest (signature check) ─► SQS FIFO (per-user order, DLQ)
                                                                  │
                                                         Lambda worker
        ┌────────────┬──────────────┬─────────────┬───────────────┼──────────────┐
     S3 photos    Textract      Bedrock        DynamoDB       Polly (voice)   Strands agent
     + PDFs       (OCR)       (vision, chat)  (single table)                  (Bedrock)
                                  Open-Meteo + NASA POWER (irradiance, rain, temperature)

EventBridge Scheduler ─► nudge (19:00 IST) · audit (02:30 IST, NASA cross-check, vendor stats)
S3 website hosting ─► site;  API Gateway ─► public API (/api/vendors, /api/verify, /api/promise)
```
AWS open-source: SAM, Strands Agents SDK. Infrastructure is one file: `template.yaml`.

## Configuration

`web/config.js` holds `SURYA_API` (written by `deploy.ps1`) and `SURYA_WA`, the bot's WhatsApp number (digits with country code) used for the *Chat on WhatsApp* links. During the hackathon the bot runs on a Meta test number, which replies only to registered testers.

## Run it locally (no AWS needed)

```powershell
python -m pip install -r app/requirements.txt pytest
python -m pytest                       # 43 tests
python app/local_chat.py               # chat with the bot in your terminal, live weather
cd web; python -m http.server 8765     # website (sample data until deployed)
```

## Deploy

1. **AWS account**, claim the hackathon credits, `aws configure` (region `ap-south-1`).
2. **Bedrock**: models are enabled automatically on first use (the Model access page is retired). Brand-new AWS accounts can have Textract and Bedrock restricted for a few hours while AWS verifies them.
3. **WhatsApp Cloud API test number** (free, no business verification):
   developers.facebook.com → Create app → Business → add *WhatsApp*. Under *API setup* copy the **temporary access token** and **Phone number ID**, add your phone (and up to 4 others) as recipients. Under *App settings → Basic* copy the **App secret**. Make up a **verify token**.
   ```powershell
   $env:WA_TOKEN="…"; $env:WA_PHONE_ID="…"; $env:WA_APP_SECRET="…"; $env:WA_VERIFY_TOKEN="any-secret-string"
   ```
   (The temporary token expires in 24 h; for the judging window create a *System User* token with `whatsapp_business_messaging`.)
4. `./deploy.ps1`. It builds in Docker, deploys, publishes the site to S3, seeds demo systems, and prints the **Webhook URL**.
5. In Meta → WhatsApp → *Configuration*: paste the Webhook URL and your verify token, subscribe to **messages**.
6. Message the test number "hi".

## Facts

- **Photo reading is implemented and unit-tested with stubbed Textract/Bedrock responses, but was not yet exercised against the live services**: the AWS account was still in AWS's new-account verification when this was submitted. Typed readings work end to end on WhatsApp.
- The webhook verifies Meta's `X-Hub-Signature-256` only when the Meta app secret is configured (`WA_APP_SECRET`); the demo deployment ran without it, so treat it as unauthenticated until that is set.
- The site is served over plain HTTP from S3 website hosting, because CloudFront was blocked on the new account.
- Demo systems are **simulated** (real weather history, injected faults) and labelled so everywhere. The real-owner flow uses real readings.
- Output ranges are engineering estimates: healthy performance ratio 0.72–0.80, heat derating -0.4 %/°C. They are not legal findings.
- The net-meter check is a heuristic (assumes ≤ 6 units/day of daytime self-consumption).
