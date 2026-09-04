# Point Agent Mesh at a broker, without re-running `sam init`.
#
#   .\configure-sam.ps1            # local broker
#   .\configure-sam.ps1 -Cloud     # Solace Cloud
#
# WHY THIS IS SEPARATE FROM setup-sam.ps1
# setup-sam.ps1 re-runs `sam init`, which regenerates every config file and
# overwrites .env without backup. That is right for first-time scaffolding and
# wrong for "the broker moved". This script edits only the broker settings and
# leaves the LLM configuration, the API key and the generated YAML alone.

param([switch]$Cloud)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$profileName = if ($Cloud) { "cloud" } else { "local" }
$profileFile = ".env.$profileName"

if (-not (Test-Path $profileFile)) { Write-Host "Missing $profileFile" -ForegroundColor Red; exit 1 }

function Read-Profile($file) {
    $h = @{}
    foreach ($l in (Get-Content $file)) {
        $l = $l.Trim()
        if ($l -and -not $l.StartsWith("#") -and $l.Contains("=")) {
            $k, $v = $l.Split("=", 2)
            $h[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
        }
    }
    return $h
}
$cfg = Read-Profile $profileFile

# --- the trust store -------------------------------------------------------
# SAM's connector defaults trust-store-path to os.path.dirname(certifi.where())
# - see solace_ai_connector/common/messaging/solace_messaging.py. That is the
# BROKEN configuration: certifi's package directory contains .py files as well
# as the bundle, and a trust store directory holding anything but certificates
# makes the TLS handshake succeed while long-lived receivers die in a loop of
#   SSL 'SSL-client' cannot read, sslErr = 1
# Agents ARE long-lived receivers, so the default would fail exactly where it
# hurts. The same trap cost an hour on the Act 2 consumers. certs/ holds the
# bundle and nothing else; TRUST_STORE overrides the default.
$certDir = Join-Path $PSScriptRoot "certs"
if (-not (Test-Path (Join-Path $certDir "ca-bundle.pem"))) {
    New-Item -ItemType Directory -Path $certDir -Force | Out-Null
    & .\.venv\Scripts\python.exe -c "import certifi,shutil,os; shutil.copy(certifi.where(), os.path.join(r'$certDir','ca-bundle.pem'))"
    Write-Host "  created certs\ca-bundle.pem"
}

# --- what SAM needs --------------------------------------------------------
$settings = [ordered]@{
    "SOLACE_BROKER_URL"      = $cfg["SOLACE_HOST"]
    "SOLACE_BROKER_VPN"      = $cfg["SOLACE_VPN"]
    "SOLACE_BROKER_USERNAME" = $cfg["SOLACE_USER"]
    "SOLACE_BROKER_PASSWORD" = $cfg["SOLACE_PASSWORD"]
    "SOLACE_DEV_MODE"        = "false"
    "TRUST_STORE"            = $certDir

    # DURABLE AGENT QUEUES. This is the setting Act 3's central claim depends on.
    # shared_config.yaml carries temporary_queue: ${USE_TEMPORARY_QUEUES, true},
    # and agent/sac/app.py binds each agent to {namespace}/q/a2a/{agent_name}.
    # A temporary queue is destroyed when its client disconnects, so with the
    # default the kill-an-agent beat LOSES the message and the argument collapses
    # live. False makes the queue outlive the agent, the broker spools while it is
    # gone, and the message is delivered on reconnect.
    "USE_TEMPORARY_QUEUES"   = "false"
}

# --- rewrite only those keys, preserve everything else ---------------------
$env_lines = if (Test-Path ".env") { Get-Content ".env" } else { @() }
$out = New-Object System.Collections.Generic.List[string]
$seen = @{}

foreach ($line in $env_lines) {
    $t = $line.Trim()
    if ($t -and -not $t.StartsWith("#") -and $t.Contains("=")) {
        $k = $t.Split("=", 2)[0].Trim()
        if ($settings.Contains($k)) {
            $out.Add("$k=`"$($settings[$k])`"")
            $seen[$k] = $true
            continue
        }
    }
    $out.Add($line)
}
foreach ($k in $settings.Keys) {
    if (-not $seen.ContainsKey($k)) { $out.Add("$k=`"$($settings[$k])`"") }
}
# WRITE WITHOUT A BOM.
# PowerShell 5.1's `Set-Content -Encoding utf8` emits a UTF-8 BOM. SAM reads
# .env itself and does not strip it, so the first key becomes "﻿NAMESPACE"
# instead of "NAMESPACE" - and the run dies with
#   ValidationError: 1 validation error for SamAgentAppConfig / namespace / Field required
# which points at the YAML, not at the encoding of a different file. This bit
# once already, on 2026-09-02, and it is the same trap src/profile.py reads
# around with utf-8-sig.
[System.IO.File]::WriteAllLines(
    (Join-Path $PSScriptRoot ".env"),
    $out,
    (New-Object System.Text.UTF8Encoding $false))

Write-Host ""
Write-Host ("Agent Mesh pointed at: {0}" -f $profileName) -ForegroundColor Green
Write-Host ("  broker        {0}" -f $settings["SOLACE_BROKER_URL"])
Write-Host ("  vpn           {0}" -f $settings["SOLACE_BROKER_VPN"])
Write-Host ("  trust store   {0}" -f $settings["TRUST_STORE"])
Write-Host ("  agent queues  durable (USE_TEMPORARY_QUEUES=false)") -ForegroundColor Green
Write-Host ""
Write-Host "Run it with:  .\.venv-sam\Scripts\solace-agent-mesh.exe run"
Write-Host ""
