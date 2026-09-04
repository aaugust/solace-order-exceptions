# Take the demo environment down and reclaim resources.
#
# Works for both profiles. In cloud mode there is no container to stop - the
# script says so rather than silently doing less, because "down" reporting
# success while a remote broker keeps running would be misleading.
#
#   .\demo-down.ps1             # free RAM and CPU, keep everything  (resume ~90s)
#   .\demo-down.ps1 -Destroy    # also delete the container          (next up = ~3min)
#   .\demo-down.ps1 -Deep       # also stop Docker Desktop + its WSL backend VM
#
# THERE IS NO SEPARATE "PAUSE" AND "STOP", because for resources they are the
# same operation. `docker stop` is what gives back the RAM and the CPU, and it
# gives back all of it. Measured on this machine, the idle broker holds ~1.5 GB
# and ~37% CPU; stopping reclaims both.
#
# What -Destroy adds is DISK, not resources: the container's writable layer is
# roughly 878 MB after a few days of broker logs and spool. The 2.48 GB image
# stays either way. The cost is that queues, subscriptions and DMQ wiring go
# with it, so the next demo-up has to re-provision.
#
# Use plain down almost always. Use -Destroy when you want a guaranteed clean
# rebuild, or you actually need the disk back.

param([switch]$Destroy, [switch]$Deep)

$ErrorActionPreference = "Continue"

# Match the demo's own processes by this repo's directory name, resolved at
# runtime rather than hard-coded, so a clone under any name still tears down
# cleanly. Escaped because it is used as a regex.
$RepoPattern = [regex]::Escape((Split-Path -Leaf $PSScriptRoot))
Set-Location $PSScriptRoot

$CONTAINER = "solace"

Write-Host ""
Write-Host "Meridian demo environment - down" -ForegroundColor Cyan
Write-Host ""

# --- how much are we about to reclaim? -------------------------------------
$mem = 0
foreach ($line in (docker stats --no-stream --format "{{.Name}}|{{.MemUsage}}" 2>$null)) {
    if ($line -match "^$CONTAINER\|([\d.]+)(GiB|MiB)") {
        $mem = if ($Matches[2] -eq "GiB") { [double]$Matches[1] * 1024 } else { [double]$Matches[1] }
    }
}

# --- 1. demo terminal windows ----------------------------------------------
# Matched on the titles start-demo.ps1 sets, so other PowerShell sessions on
# this machine are left alone.
# WHY NOT MainWindowTitle. It does not work under Windows Terminal, the default
# host on Windows 11: the panes are owned by WindowsTerminal.exe, so every
# powershell.exe reports MainWindowHandle 0 and an EMPTY MainWindowTitle. The
# title comparison matched nothing and truthfully reported "0 closed" while all
# four windows stayed open. Found 2026-09-02.
#
# WHY NOT MATCH ON THE TITLES EITHER. Doing that made the stop path break every
# time a window was renamed - windows opened before a rename could no longer be
# closed by the script that renamed them. Match on what does not change: a
# window opened by start-demo.ps1 is a powershell.exe launched with -NoExit,
# whose command line sets RawUI.WindowTitle and cd's into this repo.
#
# $PID is excluded because a shell that merely MENTIONS these strings - this
# script, a grep, a diagnostic - would otherwise match and kill itself. That
# happened on 2026-09-02 with a different pattern and cost repeated exit 255s.
$closed = 0
@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -eq 'powershell.exe' -and
                 $_.ProcessId -ne $PID -and
                 $_.CommandLine -and
                 $_.CommandLine -match 'RawUI\.WindowTitle' -and
                 $_.CommandLine -match $RepoPattern -and
                 $_.CommandLine -match '-NoExit' }) |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $closed++ }
Write-Host ("  demo windows closed        {0}" -f $closed)

# --- 2. SAM and stray demo python ------------------------------------------
# Matched on the COMMAND LINE, not the image name: killing python.exe broadly
# would take out unrelated Python on this machine.
# The old pattern required '<repo>\src\consumer.py' to appear in the
# command line. It never does. start-demo.ps1 cd's into the repo and runs
# `python src\consumer.py`, so the repo path is on the INTERPRETER and the script
# argument is RELATIVE - the two halves are never adjacent. The script reported
# "0 processes ended" while six consumers kept running and kept consuming, which
# is how a queue could look empty after a demo-down that had stopped nothing.
# Found 2026-09-02.
#
# Matching the repo path alone is enough: Name is already constrained to the
# Python interpreters below, so any python.exe running out of this repo is by
# definition part of the demo.
$pattern = "venv-sam|solace-agent-mesh|$RepoPattern"
# FILTER ON PROCESS NAME TOO, not just the command line. A PowerShell process
# whose own command line contains the search pattern matches itself - so this
# script would kill the shell running it, and exit 255. That happened repeatedly
# on 2026-09-02 before it was noticed. Only python.exe and the SAM launcher are
# ever the target.
$procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
           Where-Object { $_.Name -in @('python.exe','pythonw.exe','solace-agent-mesh.exe') -and
                          $_.CommandLine -and $_.CommandLine -match $pattern })
foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host ("  SAM / demo processes ended {0}" -f $procs.Count)

# --- 3. the broker ---------------------------------------------------------
$state = (docker inspect $CONTAINER --format '{{.State.Status}}' 2>$null)
if ($LASTEXITCODE -ne 0) {
    Write-Host "  broker                     no local container"
    Write-Host "  (If you were running -Cloud, the remote service is still up and"
    Write-Host "   still counting against the trial. Delete it in console.solace.cloud"
    Write-Host "   when you are finished with it.)"
} else {
    if ($state -eq "running") {
        docker stop $CONTAINER | Out-Null
        Write-Host ("  broker stopped             ~{0} MB and ~37% CPU reclaimed" -f [int]$mem) -ForegroundColor Green
    } else {
        Write-Host "  broker                     already $state"
    }

    if ($Destroy) {
        $size = (docker ps -a --filter "name=$CONTAINER" --format '{{.Size}}' 2>$null)
        docker rm $CONTAINER | Out-Null
        Write-Host ("  container deleted          {0} disk reclaimed" -f $size) -ForegroundColor Green
        Write-Host "  Queues and subscriptions went with it. Next demo-up will re-provision."
    }
}

# --- 4. optional deep stop -------------------------------------------------
if ($Deep) {
    # Docker Desktop is shared infrastructure. Shutting it down stops every
    # OTHER container too, so check before reaching outside this project.
    $others = @(docker ps --format "{{.Names}}" 2>$null | Where-Object { $_ -and $_ -ne $CONTAINER })
    if ($others.Count -gt 0) {
        Write-Host ""
        Write-Host "  DEEP STOP REFUSED - other containers are running:" -ForegroundColor Red
        $others | ForEach-Object { Write-Host "    $_" }
        Write-Host "  Shutting down Docker Desktop would stop these too. Stop them yourself"
        Write-Host "  first, or skip -Deep: the broker is already stopped, which is most of"
        Write-Host "  the saving."
    } else {
        Write-Host ""
        Write-Host "  shutting down Docker Desktop..."
        # USE DOCKER'S OWN CLI, NOT Stop-Process.
        # Killing the Docker Desktop process does not work: it has a restart
        # supervisor and comes straight back, taking the WSL VM with it. Measured
        # 2026-09-02 - Stop-Process left vmmemWSL at 1,803 MB and Docker back at
        # 921 MB within seconds. `docker desktop stop` is the supported shutdown
        # and took the same machine to 658 MB / 0 MB.
        docker desktop stop 2>$null | Out-Null
        Start-Sleep -Seconds 8

        # WHY WSL IS INVOLVED AT ALL: Agent Mesh runs natively on Windows and
        # needs no WSL. But Docker Desktop uses WSL2 as its backend, and the
        # broker container lives inside the `docker-desktop` distro. That VM
        # (vmmemWSL) is the single largest consumer here - measured at ~2.1 GB -
        # because it holds memory it has allocated even after containers release
        # it.
        #
        # Terminate ONLY Docker's distro. `wsl --shutdown` would take down every
        # distro on the machine, including an Ubuntu install (still the
        # documented SAM path and a live fallback), with no warning.
        $distros = @(wsl -l -q 2>$null | ForEach-Object { ($_ -replace "`0", "").Trim() } |
                     Where-Object { $_ })
        $others  = @($distros | Where-Object { $_ -ne "docker-desktop" })

        wsl --terminate docker-desktop 2>$null | Out-Null
        Write-Host "  Docker Desktop stopped     ~2 GB more reclaimed (WSL backend VM)" -ForegroundColor Green

        if ($others.Count -gt 0) {
            Write-Host ("  left running: {0}" -f ($others -join ", "))
        }
    }
}

Write-Host ""
if ($Destroy) {
    Write-Host "Down. Container removed; next start is a cold one." -ForegroundColor Green
} else {
    Write-Host "Down. Queues, subscriptions and spooled messages are intact." -ForegroundColor Green
}
Write-Host "Back up with:  .\demo-up.ps1"
Write-Host ""

# EXIT 0 EXPLICITLY. Every `docker` probe above runs whether or not Docker is
# running, and a failed probe sets $LASTEXITCODE - which becomes this script's
# exit code, so a completely successful cloud-mode teardown reported failure.
# The probes are best-effort by design (that is why they redirect stderr), so
# their exit codes must not leak out as this script's result.
exit 0
