# Bring the demo environment up, from whatever state it is in.
#
#   .\demo-up.ps1             # DEFAULT: Solace Cloud + the four demo windows
#   .\demo-up.ps1 -Local      # fallback: the local Docker broker
#   .\demo-up.ps1 -NoWindows  # provision only, open nothing
#   .\demo-up.ps1 -Fresh      # wipe queue contents first (clears rehearsal backlog)
#
# WHY CLOUD AND WINDOWS ARE THE DEFAULT
# An earlier version defaulted to local, on the reasoning that local was the
# proven path and an unverified default is how you discover a problem at the
# worst moment. That reasoning was right at the time and is now spent: cloud
# has been run end to end, and defaulting to it means the command you type
# under pressure is the one you rehearsed. Local remains the fallback for a bad
# network on the day, one switch away and otherwise unchanged.
#
# -Cloud and -Windows are still ACCEPTED and ignored. They are all over the
# runbook and shell history, and a flag that used to work erroring out thirty
# seconds before a demo is a self-inflicted wound.
#
# ONE SCRIPT FOR BOTH COLD START AND RESUME. It checks what already exists and
# does only what is missing:
#
#   container absent   -> docker run, then provision   (~3 min, cold start)
#   container stopped  -> docker start, then provision (~90 s, resume)
#   container running  -> provision only               (seconds)
#
# Provisioning runs every time. It is reconciling, so it is a no-op when the
# broker already matches the definitions, and a repair when it does not.
#
# There is no separate "start" and "resume" because the only difference is
# whether the container exists, and the script can see that for itself.

param(
    [switch]$Local,       # use the local Docker broker instead of Solace Cloud
    [switch]$NoWindows,   # provision only, do not open the demo windows
    [switch]$Fresh,
    # Accepted and ignored. They are the defaults now, but they appear in the
    # runbook, in muscle memory and in shell history, so typing them must keep
    # working rather than erroring out thirty seconds before a demo.
    [switch]$Cloud,
    [switch]$Windows
)

# CLOUD AND WINDOWS ARE THE DEFAULT PATH. The local Docker broker is the
# fallback for a bad network on the day, and it is opt-in via -Local.
$Cloud   = -not $Local
$Windows = -not $NoWindows

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$CONTAINER = "solace"
$IMAGE     = "solace/solace-pubsub-standard"
# Read connection settings from the selected profile rather than hardcoding
# local values, so the readiness check and provisioning follow -Cloud too.
function Read-Profile($file) {
    $h = @{}
    if (Test-Path $file) {
        foreach ($l in (Get-Content $file)) {
            $l = $l.Trim()
            if ($l -and -not $l.StartsWith("#") -and $l.Contains("=")) {
                $k, $v = $l.Split("=", 2)
                $h[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
            }
        }
    }
    return $h
}

# The profile decides which broker EVERYTHING talks to - scripts, publisher,
# consumers, provisioning. Set once here and inherited by every child process,
# so provisioning cannot land on one broker while the demo runs against another.
$env:DEMO_PROFILE = if ($Cloud) { "cloud" } else { "local" }

# Read-Profile above is only useful if it is actually called. $SEMPBASE and
# $AUTH are what the readiness check and every SEMP call below depend on; when
# they were left unset the check requested a relative URI with no credentials,
# failed on every attempt, and spent three minutes reporting a credential
# problem that did not exist. Bind them here, from the profile just selected.
$PROFILE_FILE = if ($Cloud) { ".env.cloud" } else { ".env.local" }
$P = Read-Profile $PROFILE_FILE
$SEMPBASE = $P["SOLACE_SEMP"]
if (-not $SEMPBASE) {
    Write-Host ("  SOLACE_SEMP is not set in {0}." -f $PROFILE_FILE) -ForegroundColor Red
    exit 1
}
$SEMPBASE = $SEMPBASE.TrimEnd("/")
$pair  = "{0}:{1}" -f $P["SOLACE_ADMIN_USER"], $P["SOLACE_ADMIN_PASSWORD"]
$b64   = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
$AUTH  = @{ Authorization = "Basic $b64" }

Write-Host ""
Write-Host ("Meridian demo environment - up  [{0}]" -f $env:DEMO_PROFILE) -ForegroundColor Cyan
Write-Host ""

if ($Cloud) {
    # Nothing to start: the broker is somebody else's problem, which is the
    # entire point of this mode. Docker stays off and the WSL VM stays unborn.
    if (-not (Test-Path ".env.cloud")) {
        Write-Host "  .env.cloud not found." -ForegroundColor Red
        exit 1
    }
    $blank = @(Get-Content .env.cloud | Select-String '^[A-Z_]+=\s*$')
    if ($blank.Count -gt 0) {
        Write-Host "  .env.cloud has empty values:" -ForegroundColor Red
        $blank | ForEach-Object { Write-Host ("    " + ($_ -replace '=.*','')) }
        Write-Host "  Fill them from the Solace Cloud console before using -Cloud."
        exit 1
    }
    Write-Host "  broker                     Solace Cloud (no local container)" -ForegroundColor Green
}

# --- 1. Docker daemon (local only) -----------------------------------------
if (-not $Cloud) {
docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Docker daemon down, starting Docker Desktop..."
    $dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $dd)) { Write-Host "  Not found: $dd" -ForegroundColor Red; exit 1 }
    Start-Process $dd
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
        Write-Host "    waiting for the daemon..."
    }
    if ($LASTEXITCODE -ne 0) { Write-Host "  Daemon did not start." -ForegroundColor Red; exit 1 }
}
Write-Host "  docker daemon              up" -ForegroundColor Green
}

# --- 2. container: create, start, or leave alone (local only) --------------
if (-not $Cloud) {
$state = (docker inspect $CONTAINER --format '{{.State.Status}}' 2>$null)

if ($LASTEXITCODE -ne 0) {
    # COLD START. --shm-size=2g is mandatory: the broker uses shared memory for
    # its spool and will not start on Docker's 64 MB default. That is the most
    # common first-run failure.
    Write-Host "  container absent, creating (cold start)..."
    docker run -d --name=$CONTAINER --shm-size=2g `
        -p 8080:8080 -p 55555:55555 -p 8008:8008 -p 1883:1883 -p 5672:5672 -p 9000:9000 `
        --env username_admin_globalaccesslevel=admin --env username_admin_password=admin `
        $IMAGE | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "  docker run failed." -ForegroundColor Red; exit 1 }
    Write-Host "  container created" -ForegroundColor Green
}
elseif ($state -ne "running") {
    docker start $CONTAINER | Out-Null
    Write-Host "  container started          (was $state)" -ForegroundColor Green
}
else {
    Write-Host "  container                  already running"
}
}

# --- 3. wait for the broker to ANSWER --------------------------------------
# Container status is not readiness: it reports running 60-90s before SEMP
# responds. And the startup logs are a wall of cosmetic ASSERT errors that look
# like failures, so they are useless as a signal. Poll the management API.
Write-Host ("  waiting for SEMP ({0})" -f $(if ($Cloud) {"cloud"} else {"local"})) -NoNewline
$deadline = (Get-Date).AddMinutes(3)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $null = Invoke-RestMethod -Uri "$SEMPBASE/about/api" -Headers $AUTH -TimeoutSec 5 -ErrorAction Stop
        $ready = $true; break
    } catch { Write-Host "." -NoNewline; Start-Sleep -Seconds 5 }
}
Write-Host ""
if (-not $ready) {
    Write-Host "  SEMP did not answer within 3 minutes." -ForegroundColor Red
    if ($Cloud) {
        Write-Host "  Check the service is Running in console.solace.cloud, and that"
        Write-Host "  SOLACE_SEMP / SOLACE_ADMIN_* in .env.cloud are the MANAGEMENT"
        Write-Host "  credentials - they are a different account from the messaging ones."
    } else {
        $rc = docker inspect $CONTAINER --format '{{.RestartCount}}' 2>$null
        Write-Host "  RestartCount = $rc. Non-zero means a real fault (usually --shm-size)."
        Write-Host "  The ERROR lines in 'docker logs' are cosmetic startup noise, not the problem."
    }
    exit 1
}
Write-Host "  broker                     answering" -ForegroundColor Green

# --- 4. topology -----------------------------------------------------------
# ALWAYS provision. An earlier version checked whether the three service queues
# existed and skipped provisioning if they did, which was too shallow a test: it
# never looked at the dead message queue, at whether the topic subscriptions
# were still attached, or at whether max-redelivery and deadMsgQueue were still
# correct. A queue can exist with no subscriptions, and that broker looks right
# and routes nothing.
#
# provision.py is idempotent AND reconciling - it PATCHes anything that already
# exists back to the definition in the file - so running it unconditionally is
# both cheaper to reason about and strictly more correct. It takes about two
# seconds, which is not worth optimising away.
#
# The definitions live in scripts\provision.py and src\topics.py, in the repo.
# Nothing on the broker is a source of truth, which is why -Destroy is safe.
if ($Fresh) {
    Write-Host "  -Fresh: removing queues so they come back empty..."
    & .\.venv\Scripts\python.exe scripts\provision.py --teardown | Out-Null
}

Write-Host "  provisioning..." -NoNewline
$prov = & .\.venv\Scripts\python.exe scripts\provision.py 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  PROVISIONING FAILED" -ForegroundColor Red
    $prov | ForEach-Object { Write-Host "    $_" }
    exit 1
}
# Report what actually happened rather than a bare "ok" - created on a cold
# start, updated if something had drifted, exists if nothing changed.
$created = @($prov | Select-String -Pattern '^\s+created' ).Count
$updated = @($prov | Select-String -Pattern '^\s+updated' ).Count
$existed = @($prov | Select-String -Pattern '^\s+exists'  ).Count
Write-Host ""
Write-Host ("  queues + subscriptions     {0} created, {1} updated, {2} already correct" -f `
            $created, $updated, $existed) -ForegroundColor Green

# --- 5. windows ------------------------------------------------------------
if ($Windows) {
    Write-Host ""
    & (Join-Path $PSScriptRoot "start-demo.ps1") -SkipChecks
} else {
    Write-Host ""
    Write-Host "Ready." -ForegroundColor Green
    Write-Host "  Demo windows:   .\start-demo.ps1"
    Write-Host "  Agent Mesh:     .\.venv-sam\Scripts\solace-agent-mesh.exe run"
    Write-Host "  Console:        http://localhost:8080   (admin/admin)"
    Write-Host "  Down:           .\demo-down.ps1"
    if (-not $Cloud) { Write-Host "  Cloud instead:  .\demo-up.ps1   (default; no Docker, no local RAM)" }
    Write-Host ""
}
