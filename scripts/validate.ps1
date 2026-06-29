# Validate all production artifacts (Windows PowerShell)
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "=== pytest ===" -ForegroundColor Cyan
if (-not $SkipTests) {
    python -m pytest tests/ -m "unit or integration or regression" -q --tb=line
}

Write-Host "`n=== ruff ===" -ForegroundColor Cyan
python -m pip install ruff -q
python -m ruff check .
python -m ruff format --check .

Write-Host "`n=== helm lint ===" -ForegroundColor Cyan
helm lint ./helm
helm lint ./helm -f helm/values-prod.yaml
helm lint ./helm -f helm/values-docker-desktop.yaml

Write-Host "`n=== kubectl dry-run (offline) ===" -ForegroundColor Cyan
kubectl kustomize k8s/ | kubectl apply --dry-run=client --validate=false -f -

Write-Host "`n=== All validations passed ===" -ForegroundColor Green
