# One-shot deploy: build (in Docker so Lambda gets Linux wheels), deploy the stack,
# publish the website with the live API URL, and seed the demo systems.
#
# Prereqs: aws CLI configured (aws configure), Docker running, SAM CLI installed.
# WhatsApp keys are optional on the first run: without them the website, dashboard and API go live,
# and the bot starts working once you re-run with these set (README step 3):
#   $env:WA_VERIFY_TOKEN, $env:WA_APP_SECRET, $env:WA_TOKEN, $env:WA_PHONE_ID
param(
  [string]$Region = "ap-south-1",
  [string]$Stack = "surya-sach",
  [switch]$SkipSeed,
  [switch]$SkipBuild
)
$ErrorActionPreference = "Stop"
$env:SAM_CLI_TELEMETRY = "0"
Set-Location $PSScriptRoot

function Find($name, $fallback) {
  $c = Get-Command $name -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  if (Test-Path $fallback) { return $fallback }
  throw "$name not found"
}
$sam = Find "sam" "$env:APPDATA\Python\Python314\Scripts\sam.exe"
$aws = Find "aws" "C:\Program Files\Amazon\AWSCLIV2\aws.exe"

$wa = @{}
foreach ($v in "WA_VERIFY_TOKEN", "WA_APP_SECRET", "WA_TOKEN", "WA_PHONE_ID") {
  $val = [Environment]::GetEnvironmentVariable($v)
  if (-not $val) { $val = "pending-setup"; Write-Host "$v not set: deploying without WhatsApp for now" }
  $wa[$v] = $val
}

$dash = $env:DASH_SECRET
if (-not $dash) { $dash = -join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ }) }

if (-not $SkipBuild) { & $sam build --use-container; if ($LASTEXITCODE) { throw "sam build failed" } }
& $sam deploy --stack-name $Stack --region $Region --resolve-s3 --capabilities CAPABILITY_IAM --no-confirm-changeset --no-fail-on-empty-changeset `
  --parameter-overrides "WaVerifyToken=$($wa.WA_VERIFY_TOKEN)" "WaAppSecret=$($wa.WA_APP_SECRET)" "WaToken=$($wa.WA_TOKEN)" "WaPhoneId=$($wa.WA_PHONE_ID)" "DashSecret=$dash"
if ($LASTEXITCODE) { throw "sam deploy failed" }

function Out($k) { & $aws cloudformation describe-stacks --stack-name $Stack --region $Region --query "Stacks[0].Outputs[?OutputKey=='$k'].OutputValue" --output text }
$api = Out ApiUrl; $site = Out SiteUrl; $bucket = Out WebBucketName; $table = Out TableName; $hook = Out WebhookUrl

$cfg = Get-Content web/config.js -Raw
$cfg = [regex]::Replace($cfg, 'window\.SURYA_API = "[^"]*";', "window.SURYA_API = `"$api`";")
Set-Content -Path web/config.js -Value $cfg -Encoding utf8 -NoNewline
& $aws s3 sync web/ "s3://$bucket" --region $Region --delete
if (-not $SkipSeed) { Push-Location app; python -m seed.load --table $table; Pop-Location }

Write-Host "`nSite:        $site"
Write-Host "Webhook URL: $hook   (paste into Meta > WhatsApp > Configuration, verify token = your WA_VERIFY_TOKEN)"
