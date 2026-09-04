# Open the demo window set: three consumers running, publisher ready to type in.
#
# Run this, not four manual launches. It sets the working directory, activates
# the virtualenv and titles each window, so nothing has to be typed under
# pressure except the beat commands themselves.
#
#   .\start-demo.ps1              # normal
#   .\start-demo.ps1 -SkipChecks  # skip the broker preflight
#
# Double-clickable equivalent: start-demo.cmd

param([switch]$SkipChecks)

$Root = $PSScriptRoot

# Match the demo's own processes by this repo's directory name, resolved at
# runtime rather than hard-coded, so a clone under any name still tears down
# cleanly. Escaped because it is used as a regex.
$RepoPattern = [regex]::Escape((Split-Path -Leaf $PSScriptRoot))
$Py   = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) {
    Write-Host "No virtualenv at $Py" -ForegroundColor Red
    Write-Host "Run:  python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt"
    exit 1
}

# THE WINDOWS MUST BE TOLD WHICH BROKER. Launched from demo-up.ps1 they inherit
# $env:DEMO_PROFILE; run standalone they inherited nothing and every consumer
# silently aimed at the LOCAL broker, failing with "Connection refused (10061)".
# Four windows all failing that way looks like a broker outage and is not one.
# Set it here if unset, and bake it into each window's command so the window is
# explicit rather than dependent on what it happened to inherit.
if (-not $env:DEMO_PROFILE) { $env:DEMO_PROFILE = "cloud" }
Write-Host ("Profile: {0}" -f $env:DEMO_PROFILE) -ForegroundColor Cyan

# --- preflight -------------------------------------------------------------
# Better to find a stopped broker here than mid-demo. Checks SEMP
# rather than the container logs, which are full of cosmetic startup errors.
if (-not $SkipChecks) {
    Write-Host "Checking broker..." -NoNewline
    # READ THE PROFILE. This used to hardcode localhost:8080 and admin:admin, so
    # running start-demo.ps1 standalone against the cloud broker reported "NOT
    # RESPONDING" and advised `docker start solace` - checking a broker that is
    # not the one the demo uses. demo-up.ps1 passes -SkipChecks, so the fault
    # only showed on the standalone path. Found 2026-09-02.
    $profileFile = if ($env:DEMO_PROFILE -eq "local") { ".env.local" } else { ".env.cloud" }
    $cfg = @{}
    foreach ($l in (Get-Content (Join-Path $Root $profileFile) -ErrorAction SilentlyContinue)) {
        $l = $l.Trim()
        if ($l -and -not $l.StartsWith("#") -and $l.Contains("=")) {
            $k, $v = $l.Split("=", 2); $cfg[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
        }
    }
    try {
        $pair = "{0}:{1}" -f $cfg["SOLACE_ADMIN_USER"], $cfg["SOLACE_ADMIN_PASSWORD"]
        $auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
        $null = Invoke-RestMethod -Uri ($cfg["SOLACE_SEMP"].TrimEnd("/") + "/about/api") `
                                  -Headers @{Authorization = "Basic $auth"} `
                                  -TimeoutSec 8 -ErrorAction Stop
        Write-Host " up" -ForegroundColor Green
    } catch {
        Write-Host " NOT RESPONDING" -ForegroundColor Red
        Write-Host ""
        Write-Host ("  profile:    {0}" -f $profileFile)
        if ($profileFile -eq ".env.cloud") {
            Write-Host "  Check the service is Running in console.solace.cloud."
        } else {
            Write-Host "  Start it:   docker start solace"
            Write-Host "  Or create:  see RUNBOOK.md section 0"
            Write-Host "  It needs 60-90s after starting before SEMP answers."
        }
        Write-Host ""
        Write-Host "  (Ignore the wall of ERROR lines in docker logs - they are cosmetic.)"
        exit 1
    }

    Write-Host "Provisioning..." -NoNewline
    $prov = & $Py (Join-Path $Root "scripts\provision.py") 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host " FAILED" -ForegroundColor Red
        $prov | ForEach-Object { Write-Host "  $_" }
        exit 1
    }
    Write-Host " ok" -ForegroundColor Green
}

# --- windows ---------------------------------------------------------------
function Start-DemoWindow {
    param([string]$Title, [string]$Command, [switch]$Idle)

    # -NoExit keeps the window open when the command ends, so a consumer that
    # dies leaves its error on screen instead of vanishing.
    $inner = "`$host.ui.RawUI.WindowTitle = '$Title'; Set-Location '$Root'; " +
             "`$env:DEMO_PROFILE = '$($env:DEMO_PROFILE)'; " +
             ". .\.venv\Scripts\Activate.ps1; "
    if ($Idle) {
        $inner += "Write-Host '$Title' -ForegroundColor Cyan; " +
                  "Write-Host 'Paste beat commands here. See RUNBOOK.md section 3.'; " +
                  "Write-Host ''"
    } else {
        $inner += $Command
    }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $inner
    Start-Sleep -Milliseconds 700
}

# CLOSE ANY EXISTING SET FIRST. Opening a second set is not additive, it is
# BROKEN: every consumer connects as "meridian-<role>", the broker allows one
# client per name per VPN, and two processes claiming the same name kick each
# other off in a loop. What you see is a stream of
# SOLCLIENT_SUBCODE_COMMUNICATION_ERROR / "SSL 'SSL-client' cannot read", client
# uptime stuck at 0-1s, queues that never drain, and redelivery counts climbing
# - a failure that reads as a TLS problem and is not one.
#
# Found 2026-09-02 after demo-up was run while a set was already open. demo-up
# is otherwise idempotent by design; the windows were the one part of it that
# was not, so they are reconciled here like everything else.
$stale = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
           Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and
                          (($_.Name -eq 'powershell.exe' -and
                            $_.CommandLine -match 'RawUI\.WindowTitle' -and
                            $_.CommandLine -match $RepoPattern -and
                            $_.CommandLine -match '-NoExit') -or
                           ($_.Name -in @('python.exe','pythonw.exe') -and
                            $_.CommandLine -match $RepoPattern)) })
if ($stale.Count) {
    Write-Host ""
    Write-Host ("  closing {0} process(es) from a previous set first" -f $stale.Count) -ForegroundColor Yellow
    foreach ($x in $stale) { Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue }
    # Let the broker notice the disconnects before the new clients claim the
    # same names, or the new set races the old set's teardown.
    Start-Sleep -Seconds 3
}

Write-Host ""
Write-Host "Opening demo windows..."

Start-DemoWindow -Title "2 desk-credit"    -Command "python src\consumer.py desk-credit"
Start-DemoWindow -Title "3 desk-inventory" -Command "python src\consumer.py desk-inventory"
Start-DemoWindow -Title "4 desk-audit"     -Command "python src\consumer.py desk-audit"
# Numbered 1 because it is the window you drive from, but opened LAST on
# purpose: the last window launched lands on top and focused, so the one you
# type into is the one already in front of you.
Start-DemoWindow -Title "1 PUBLISHER"      -Idle

Write-Host ""
Write-Host "Four windows open. Window 1 is the PUBLISHER and is yours; 2-4 are the bound consumers." -ForegroundColor Green
Write-Host ""
Write-Host "  Beat 1  python src\publisher.py            (8 orders; last 2 break)"
Write-Host "  Beat 3  python src\publisher.py --poison       (only if re-running it live)"
Write-Host ""
Write-Host "  Forced singles, off-script:  --credit-hold  --shortfall  --duplicate"
Write-Host ""
if ($env:DEMO_PROFILE -eq "local") {
    Write-Host "Open http://localhost:8080 (admin/admin) as window 5."
} else {
    Write-Host "Window 5 is the broker console - on cloud that is NOT localhost:8080:"
    Write-Host "  https://mr-connection-zlmtq5pi7n8.messaging.solace.cloud:943"
    Write-Host "  sign in with SOLACE_ADMIN_USER / SOLACE_ADMIN_PASSWORD from .env.cloud"
    Write-Host "  no browser? python scripts\queue_depths.py shows the same numbers"
}
Write-Host "Stop everything with:  .\stop-demo.ps1"
Write-Host ""
