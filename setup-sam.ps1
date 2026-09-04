# Re-run `sam init` with the LLM configuration that was missed the first time.
#
# WHY THIS EXISTS
# The first init ran without its --llm-* flags because the command was pasted
# across two lines and PowerShell executed only the first half. The result is a
# project whose orchestrator references a "planning" model role that
# shared_config.yaml never defines. Re-running init with the full flag set
# regenerates the configs correctly, which is cleaner than hand-patching YAML.
#
# BEFORE RUNNING: put your Gemini key back. `sam init` overwrote .env and the
# key was lost. Fetch it again from https://aistudio.google.com/apikey (Google
# lets you view an existing key, so you do not need to create a new one), then:
#
#   Set-Content -Path .env.key -Encoding utf8 -Value "GEMINI_API_KEY=AIza..."
#
# This script reads .env.key, never prints it, and leaves it on disk so a repeat
# run does not need it re-entered. .env.key is gitignored below.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --- recover the key --------------------------------------------------------
if (-not (Test-Path ".env.key")) {
    Write-Host "Missing .env.key" -ForegroundColor Red
    Write-Host "Create it first (see the comment block at the top of this file):"
    Write-Host '  Set-Content -Path .env.key -Encoding utf8 -Value "GEMINI_API_KEY=<your key>"'
    exit 1
}
$key = ((Get-Content .env.key | Select-String '^GEMINI_API_KEY=') -replace '^GEMINI_API_KEY=', '').Trim()
if (-not $key) { Write-Host "GEMINI_API_KEY empty in .env.key" -ForegroundColor Red; exit 1 }
Write-Host ("Key loaded: {0} chars" -f $key.Length) -ForegroundColor Green

# --- keep .env.key out of the repo -----------------------------------------
if (-not (Select-String -Path .gitignore -Pattern '^\.env\.key$' -Quiet)) {
    Add-Content .gitignore ".env.key"
    Write-Host "Added .env.key to .gitignore"
}

# --- back up what init will overwrite --------------------------------------
$stamp = "backup-" + (Get-Date -Format "yyyyMMdd-HHmmss")
New-Item -ItemType Directory -Path $stamp | Out-Null
foreach ($p in @(".env", "configs")) {
    if (Test-Path $p) { Copy-Item $p -Destination $stamp -Recurse; Write-Host "Backed up $p -> $stamp\" }
}

# --- re-init, this time with the model configuration ------------------------
# Both roles point at the same model deliberately. The original plan put the
# planning role on paid Claude Sonnet because free-tier Flash was judged
# marginal for orchestration, but that judgement was made about gemini-2.5-flash
# which is no longer reachable on a new key. 3.6 Flash is three generations
# newer, so measure before paying.
#
# The openai/ prefix routes litellm to Gemini's OpenAI-compatible surface, which
# is the shape SAM's config expects. Version is pinned, not `-latest`: an alias
# resolves slowly and is a moving target under a live demo.
$sam = ".\.venv-sam\Scripts\solace-agent-mesh.exe"

$initArgs = @(
    "init", "--skip",
    "--namespace", "meridian",
    "--broker-type", "solace",
    "--broker-url", "tcp://localhost:55555",
    "--broker-vpn", "default",
    "--broker-username", "default",
    "--broker-password", "default",
    "--llm-service-endpoint", "https://generativelanguage.googleapis.com/v1beta/openai",
    "--llm-service-api-key", $key,
    "--llm-service-planning-model-name", "openai/gemini-3.6-flash",
    "--llm-service-general-model-name", "openai/gemini-3.6-flash",
    "--agent-name", "meridian_orchestrator",   # underscores only: init rejects hyphens
    "--add-webui-gateway"
)

Write-Host ""
Write-Host "Re-running sam init with LLM configuration..." -ForegroundColor Cyan
& $sam @initArgs
if ($LASTEXITCODE -ne 0) { Write-Host "init failed" -ForegroundColor Red; exit 1 }

# --- restore the Gemini key that init just overwrote again ------------------
if (-not (Select-String -Path .env -Pattern '^GEMINI_API_KEY=' -Quiet)) {
    Add-Content .env ""
    Add-Content .env "# Restored by setup-sam.ps1 (sam init rewrites this file)"
    Add-Content .env "GEMINI_API_KEY=$key"
    Write-Host "Restored GEMINI_API_KEY into .env" -ForegroundColor Green
}

# --- wire the LLM configuration that init does not write --------------------
# WHY THIS IS HERE RATHER THAN LEFT TO init:
# `sam init --skip` accepts the --llm-* flags and then discards them. Its
# shared_config template carries a models block with __PLANNING_MODEL_CONFIG__
# and __GENERAL_MODEL_CONFIG__ placeholders, and when the substitution does not
# happen the generated file drops the entire block (98 template lines -> 35
# generated). The result is configs that REFERENCE model roles nothing defines:
#   configs/agents/main_orchestrator.yaml  -> "planning"
#   configs/gateways/webui.yaml            -> "general"
#   configs/services/platform.yaml         -> general
# So we write the block ourselves. Deterministic, and visible in the repo.

$llmVars = @(
    'LLM_SERVICE_ENDPOINT="https://generativelanguage.googleapis.com/v1beta/openai"',
    "LLM_SERVICE_API_KEY=`"$key`"",
    'LLM_SERVICE_PLANNING_MODEL_NAME="openai/gemini-3.6-flash"',
    'LLM_SERVICE_GENERAL_MODEL_NAME="openai/gemini-3.6-flash"'
)
Add-Content .env ""
Add-Content .env "# LLM configuration - written by setup-sam.ps1, not by sam init."
Add-Content .env "# Gemini via its OpenAI-compatible surface; the openai/ prefix routes litellm there."
Add-Content .env "# Version pinned deliberately: an alias resolves slowly and moves under a live demo."
foreach ($v in $llmVars) { Add-Content .env $v }
Write-Host "Wrote LLM_SERVICE_* vars into .env" -ForegroundColor Green

$modelsBlock = @'

  - models:
      # Both roles point at the same model on purpose. The original plan put the
      # planning role on paid Claude Sonnet because free-tier Flash was judged
      # marginal for orchestration - but that judgement was made about
      # gemini-2.5-flash, which now 404s for new API keys. 3.6 Flash is three
      # generations newer, so measure before paying.
      planning: &planning_model
        model: ${LLM_SERVICE_PLANNING_MODEL_NAME}
        api_base: ${LLM_SERVICE_ENDPOINT}
        api_key: ${LLM_SERVICE_API_KEY}

      general: &general_model
        model: ${LLM_SERVICE_GENERAL_MODEL_NAME}
        api_base: ${LLM_SERVICE_ENDPOINT}
        api_key: ${LLM_SERVICE_API_KEY}
'@

$cfg = Get-Content configs\shared_config.yaml -Raw
if ($cfg -notmatch '(?m)^\s+- models:') {
    # Insert directly after the `shared_config:` header so the anchors are
    # defined before anything references them.
    $cfg = $cfg -replace '(?m)^(shared_config:\s*\r?\n)', ('$1' + $modelsBlock.TrimStart("`r", "`n") + "`r`n")
    Set-Content configs\shared_config.yaml -Value $cfg -Encoding utf8 -NoNewline
    Write-Host "Inserted models block into configs\shared_config.yaml" -ForegroundColor Green
} else {
    Write-Host "models block already present"
}

# --- verify -----------------------------------------------------------------
Write-Host ""
Write-Host "Verification" -ForegroundColor Cyan
$env_names = (Get-Content .env | Select-String '^[A-Z_]+=' | ForEach-Object { ($_ -split '=')[0] })
Write-Host ("  .env keys: {0}" -f ($env_names -join ', '))

foreach ($role in @('planning: &planning_model', 'general: &general_model')) {
    if (Select-String -Path configs\shared_config.yaml -Pattern ([regex]::Escape($role)) -Quiet) {
        Write-Host "  shared_config.yaml defines $($role.Split(':')[0])" -ForegroundColor Green
    } else {
        Write-Host "  MISSING: $role" -ForegroundColor Red
    }
}
foreach ($v in @('LLM_SERVICE_ENDPOINT', 'LLM_SERVICE_API_KEY', 'LLM_SERVICE_PLANNING_MODEL_NAME', 'LLM_SERVICE_GENERAL_MODEL_NAME')) {
    if (Select-String -Path .env -Pattern "^$v=" -Quiet) {
        Write-Host "  .env has $v" -ForegroundColor Green
    } else {
        Write-Host "  MISSING: $v" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Backup of the previous state is in $stamp\ - delete it once you are happy."
Write-Host "Next: .\.venv-sam\Scripts\solace-agent-mesh.exe run"
