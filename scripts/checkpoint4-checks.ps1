param(
    [switch]$SkipBuild,
    [switch]$KeepUp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

Write-Section "Stack bring-up (clean state)"
if (-not $SkipBuild) {
    # Reset volumes so the conflicting-orders test sees seed stock. The
    # books_database state lives on a host bind mount (./books_database/state/N)
    # rather than a Docker volume, so `down -v` alone doesn't reset it; we
    # delete the state files explicitly.
    & docker compose down -v | Out-Null
    foreach ($i in 1, 2, 3) {
        Get-ChildItem "books_database/state/$i" -ErrorAction SilentlyContinue |
            Remove-Item -Force -Recurse
    }
    & docker compose up --build -d | Out-Null
} else {
    & docker compose up -d | Out-Null
}

Write-Section "Pytest: tests/e2e (four Guide13 scenarios)"
# PYTHONPATH=. so 'from tests.e2e._common import ...' resolves when running
# the suite from the repo root.
$env:PYTHONPATH = "."
& python -m pytest tests/e2e -v --tb=short
$exit = $LASTEXITCODE

if (-not $KeepUp) {
    Write-Section "Tearing down stack"
    & docker compose down | Out-Null
}

if ($exit -ne 0) {
    Write-Host ""
    Write-Host "checkpoint4-checks FAILED (pytest exit=$exit)" -ForegroundColor Red
    exit $exit
}
Write-Host ""
Write-Host "checkpoint4-checks PASSED" -ForegroundColor Green
