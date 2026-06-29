#Requires -Version 5.1
<#
.SYNOPSIS
  Automated deployment for AI Inference Observability Platform on Windows.

.DESCRIPTION
  Detects environment, validates manifests, deploys via Docker Compose (primary)
  or Kubernetes/Helm (when Docker Desktop K8s is enabled), runs smoke/benchmark
  tests, and writes a readiness report.

.NOTES
  Run AFTER Docker Desktop is healthy:
    docker info
#>

param(
    [string]$ProjectRoot = $PSScriptRoot + "\..",
    [int]$ProxyPort = 8080,
    [switch]$EnableOtel,
    [switch]$UseKubernetes,
    [string]$HfToken = $env:HF_TOKEN
)

$ErrorActionPreference = "Continue"
$Report = [System.Collections.Generic.List[string]]::new()
$Failures = [System.Collections.Generic.List[string]]::new()
$Warnings = [System.Collections.Generic.List[string]]::new()

function Log($msg) { Write-Host $msg; $Report.Add($msg) }
function Fail($msg) { Write-Host "FAIL: $msg" -ForegroundColor Red; $Failures.Add($msg); $Report.Add("FAIL: $msg") }
function Warn($msg) { Write-Host "WARN: $msg" -ForegroundColor Yellow; $Warnings.Add($msg); $Report.Add("WARN: $msg") }
function Pass($msg) { Write-Host "PASS: $msg" -ForegroundColor Green; $Report.Add("PASS: $msg") }

Set-Location $ProjectRoot
Log "=== AI Inference Observability Platform — Windows Deploy ==="
Log "Project: $ProjectRoot"
Log "Time:    $(Get-Date -Format o)"

# 1. Environment
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "OS:      $([System.Environment]::OSVersion.VersionString)"
Log "User:    $(whoami)"
Log "Admin:   $isAdmin"

# 2. WSL
try {
    $wslStatus = wsl --status 2>&1 | Out-String
    Log "WSL:`n$wslStatus"
    if ($wslStatus -match "requires elevation") {
        Warn "WSL update requires Administrator. Run in elevated PowerShell: wsl --update"
    }
} catch { Warn "WSL check failed: $_" }

# 3. Docker
$dockerOk = $false
try {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true; Pass "Docker daemon reachable" }
    else {
        $st = docker desktop status 2>&1 | Out-String
        Fail "Docker daemon not ready. Status:`n$st"
        if ($st -match "wslUpdateRequired|starting") {
            Warn "Docker Desktop blocked — likely WSL update needed (Admin) or UI action required"
        }
    }
} catch { Fail "Docker not available: $_" }

# 4. kubectl / Kubernetes
$k8sOk = $false
if (Get-Command kubectl -ErrorAction SilentlyContinue) {
    $ctx = kubectl config current-context 2>&1
    Log "kubectl context: $ctx"
    kubectl get nodes 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $k8sOk = $true; Pass "Kubernetes cluster reachable" }
    else { Warn "Kubernetes not reachable (expected if Docker Desktop K8s disabled or Docker down)" }
}

# 5. Helm
$helmOk = $false
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
if (Get-Command helm -ErrorAction SilentlyContinue) {
    $helmOk = $true
    Pass "Helm installed: $(helm version --short 2>&1)"
    helm lint ./helm 2>&1 | ForEach-Object { Log $_ }
    helm template latency-metrics ./helm -n vllm 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Pass "Helm chart templates render" } else { Fail "Helm template failed" }
} else {
    Warn "Helm not found — install: winget install Helm.Helm"
}

# 6. Validate K8s manifests (offline)
kubectl kustomize k8s 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { Pass "kubectl kustomize k8s/ OK" } else { Fail "kubectl kustomize failed" }

# 7. Unit tests (no Docker required)
python -m pytest tests/ -m "unit or integration or regression" -q --tb=no 2>&1 | Tee-Object -Variable pytestOut | Out-Null
if ($LASTEXITCODE -eq 0) { Pass "Pytest: $($pytestOut[-1])" } else { Fail "Pytest failed" }

if (-not $dockerOk) {
    Fail "Cannot deploy stack — Docker Desktop must be running first."
    Log ""
    Log "=== MANUAL STEPS REQUIRED ==="
    Log "1. Open PowerShell AS ADMINISTRATOR and run:  wsl --update"
    Log "2. Restart computer (recommended) or restart WSL: wsl --shutdown"
    Log "3. Open Docker Desktop from Start menu — wait until 'Engine running'"
    Log "4. Re-run:  .\scripts\deploy-windows.ps1"
    $reportPath = Join-Path $ProjectRoot "docs\deploy-report-$(Get-Date -Format yyyyMMdd-HHmmss).md"
    ($Report -join "`n") | Set-Content $reportPath -Encoding UTF8
    Log "Report saved: $reportPath"
    exit 1
}

# 8. Deploy Docker Compose
$composeFiles = @("-f", "docker/docker-compose.yml")
if ($EnableOtel) { $composeFiles += @("-f", "docker/docker-compose.otel.yml") }

$env:PROXY_PORT = "$ProxyPort"
if ($HfToken) { $env:HF_TOKEN = $HfToken } else { Warn "HF_TOKEN not set — using public model opt-1.3b" }

Log "Deploying: docker compose $($composeFiles -join ' ') up -d --build"
docker compose @composeFiles up -d --build 2>&1 | ForEach-Object { Log $_ }

# 9. Wait for health
function Wait-Http($url, $timeoutSec = 300) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Seconds 10
    }
    return $false
}

if (Wait-Http "http://localhost:$ProxyPort/health" 300) { Pass "Proxy healthy on :$ProxyPort" } else { Fail "Proxy health timeout" }
if (Wait-Http "http://localhost:9090/-/healthy" 60) { Pass "Prometheus healthy" } else { Warn "Prometheus not ready" }
if (Wait-Http "http://localhost:3000/api/health" 60) { Pass "Grafana healthy" } else { Warn "Grafana not ready" }
if ($EnableOtel) {
    if (Wait-Http "http://localhost:16686" 60) { Pass "Jaeger UI reachable" } else { Warn "Jaeger not ready" }
}

# 10. Smoke test
try {
    $body = '{"model":"facebook/opt-1.3b","messages":[{"role":"user","content":"Hi"}],"max_tokens":5}'
    $resp = Invoke-RestMethod -Uri "http://localhost:$ProxyPort/v1/chat/completions" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 120
    if ($resp.usage.ttft_ms -or $resp.usage.e2e_latency_ms) { Pass "Smoke test: latency in usage object" }
    else { Pass "Smoke test: completion received" }
} catch { Fail "Smoke test failed: $_" }

# 11. Benchmark (light)
try {
    python benchmarks/run_benchmark.py --base-url "http://localhost:$ProxyPort" --concurrency 1 --requests-per-level 3 2>&1 | ForEach-Object { Log $_ }
    Pass "Benchmark completed"
} catch { Warn "Benchmark skipped or failed: $_" }

# 12. Kubernetes (optional)
if ($UseKubernetes -and $k8sOk) {
    kubectl create namespace vllm --dry-run=client -o yaml | kubectl apply -f -
    if ($HfToken) {
        kubectl create secret generic hf-token --from-literal=HF_TOKEN=$HfToken -n vllm --dry-run=client -o yaml | kubectl apply -f -
    } else { Warn "Skipping hf-token secret — set HF_TOKEN for vLLM on K8s" }
    if ($helmOk) {
        helm upgrade --install latency-metrics ./helm -n vllm --create-namespace 2>&1 | ForEach-Object { Log $_ }
    } else {
        kubectl apply -k k8s/ 2>&1 | ForEach-Object { Log $_ }
    }
}

# Report
Log ""
Log "=== SUMMARY ==="
Log "Failures: $($Failures.Count)  Warnings: $($Warnings.Count)"
$Failures | ForEach-Object { Log "  - $_" }
$reportPath = Join-Path $ProjectRoot "docs\deploy-report-$(Get-Date -Format yyyyMMdd-HHmmss).md"
($Report -join "`n") | Set-Content $reportPath -Encoding UTF8
Log "Report: $reportPath"
exit $(if ($Failures.Count -eq 0) { 0 } else { 1 })
