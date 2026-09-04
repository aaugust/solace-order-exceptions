# One call: stop everything, wipe every queue, start it again.
#
#   .\demo-reset.ps1            # cloud (default)
#   .\demo-reset.ps1 -Local     # local Docker broker
#
# Equivalent to demo-down followed by demo-up -Fresh, in one command. Use it
# between rehearsal runs and once before the live session.
#
# The windows are closed and reopened. An earlier version kept them in place and
# restarted the consumers inside them; that needed a supervisor loop in every
# desk window and was more machinery than the problem deserved.
#
# The consumers cannot simply be left running through a reset: resetting means
# deleting and recreating the queues, and deleting a queue TERMINATES any
# receiver bound to it. The consumer dies with "IllegalStateError: Message
# receiver already terminated" and never re-binds.
param([switch]$Local)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

if ($Local) { $env:DEMO_PROFILE = "local" }
elseif (-not $env:DEMO_PROFILE) { $env:DEMO_PROFILE = "cloud" }

Write-Host ""
Write-Host ("Meridian demo reset  [{0}]" -f $env:DEMO_PROFILE) -ForegroundColor Cyan

& (Join-Path $PSScriptRoot "demo-down.ps1") | Out-Null

# Call demo-up directly rather than splatting an argument array - splatting
# did not bind -Fresh, so the reset silently skipped the teardown and left the
# dead message queue full.
$up = Join-Path $PSScriptRoot "demo-up.ps1"
if ($Local) { & $up -Fresh -Local } else { & $up -Fresh }

exit 0
