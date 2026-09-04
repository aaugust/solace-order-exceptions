# Close the demo windows and reset the queues.
#
#   .\stop-demo.ps1          # close windows, leave queue contents
#   .\stop-demo.ps1 -Reset   # also teardown + re-provision, clearing backlogs

param([switch]$Reset)

$Root = $PSScriptRoot

# Match the demo's own processes by this repo's directory name, resolved at
# runtime rather than hard-coded, so a clone under any name still tears down
# cleanly. Escaped because it is used as a regex.
$RepoPattern = [regex]::Escape((Split-Path -Leaf $PSScriptRoot))
$Py   = Join-Path $Root ".venv\Scripts\python.exe"

# Match on the window titles set by start-demo.ps1 rather than killing every
# powershell process - this machine has other sessions open.
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
Write-Host "Closed $closed demo window(s)."

if ($Reset) {
    Write-Host "Resetting queues..."
    & $Py (Join-Path $Root "scripts\provision.py") --teardown | Out-Null
    & $Py (Join-Path $Root "scripts\provision.py")  | Out-Null
    Write-Host "Queues recreated empty." -ForegroundColor Green
}
