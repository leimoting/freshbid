$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendRoot = Join-Path $projectRoot "backend"
$frontendRoot = Join-Path $projectRoot "frontend"
$frontendIndex = Join-Path $frontendRoot "dist\index.html"
$virtualEnvironmentPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $frontendIndex)) {
    Write-Host "Building the FreshBid merchant screen..."
    Push-Location $frontendRoot
    try {
        pnpm install
        pnpm build
    }
    finally {
        Pop-Location
    }
}

$env:PYTHONPATH = Join-Path $backendRoot "src"
Write-Host "FreshBid is starting at http://127.0.0.1:8000"
Write-Host "Press Ctrl+C to stop the demo."
Set-Location $backendRoot
if (Test-Path -LiteralPath $virtualEnvironmentPython) {
    & $virtualEnvironmentPython -m freshbid.api
}
else {
    python -m freshbid.api
}
