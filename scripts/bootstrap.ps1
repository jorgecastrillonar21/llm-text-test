# Bootstrap the development environment on Windows.
#
#   powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
#
# Checks prerequisites, creates .env from the example, installs both stacks,
# migrates the database and seeds the demo world. Safe to re-run.

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail($message) {
    Write-Host "  x $message" -ForegroundColor Red
    exit 1
}

function Ok($message) {
    Write-Host "  + $message" -ForegroundColor Green
}

Write-Host "`nChecking prerequisites" -ForegroundColor Cyan

# --- Node ---------------------------------------------------------------------
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fail "node not found. Install Node 24 LTS (nvm-windows: 'nvm install 24; nvm use 24')."
}
$nodeVersion = (node --version).TrimStart('v')
$nodeMajor = [int]($nodeVersion.Split('.')[0])
if ($nodeMajor -lt 20) {
    Fail "node $nodeVersion is too old. Vite 8 needs Node 20.19+; 24 LTS is what CI uses."
}
if ($nodeMajor -lt 24) {
    Write-Host "  ! node $nodeVersion works, but CI runs Node 24" -ForegroundColor Yellow
} else {
    Ok "node $nodeVersion"
}

# --- pnpm ---------------------------------------------------------------------
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Fail "pnpm not found. Install it with 'npm install -g pnpm@9'."
}
Ok "pnpm $(pnpm --version)"

# --- Python -------------------------------------------------------------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail "python not found. Install Python 3.13 or newer."
}
$pythonVersion = (python --version).Split(' ')[1]
$pyParts = $pythonVersion.Split('.')
if ([int]$pyParts[0] -lt 3 -or ([int]$pyParts[0] -eq 3 -and [int]$pyParts[1] -lt 13)) {
    Fail "python $pythonVersion is too old. The backend requires 3.13+."
}
Ok "python $pythonVersion"

# --- uv -----------------------------------------------------------------------
# scripts/uv.mjs falls back to 'python -m uv', so a pip-installed uv that is not
# on PATH is fine. Only a completely missing uv is fatal.
$hasUv = $false
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $hasUv = $true
} else {
    try { python -m uv --version *> $null; if ($LASTEXITCODE -eq 0) { $hasUv = $true } } catch { }
}
if (-not $hasUv) {
    Write-Host "  ! uv not found - installing with 'pip install --user uv'" -ForegroundColor Yellow
    python -m pip install --user uv
    if ($LASTEXITCODE -ne 0) { Fail "uv installation failed. See https://docs.astral.sh/uv/" }
}
Ok "uv available"

# --- .env ---------------------------------------------------------------------
Write-Host "`nConfiguring" -ForegroundColor Cyan
if (Test-Path .env) {
    Ok ".env already exists (left untouched)"
} else {
    Copy-Item .env.example .env
    Ok ".env created from .env.example"
}

# --- Install ------------------------------------------------------------------
Write-Host "`nInstalling and preparing the database" -ForegroundColor Cyan
# Not 'pnpm setup' - that is pnpm's own built-in command (it configures PNPM_HOME
# and rewrites your PATH), and a package script named 'setup' would never run.
pnpm bootstrap
if ($LASTEXITCODE -ne 0) { Fail "'pnpm bootstrap' failed - see the output above." }

Write-Host "`nReady. Start everything with:" -ForegroundColor Cyan
Write-Host "  pnpm dev`n"
