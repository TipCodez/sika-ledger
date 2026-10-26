$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (!(Test-Path '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
    & '.venv\Scripts\python.exe' -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
if (!(Test-Path '.env')) { Copy-Item '.env.example' '.env' }
Write-Host 'Open http://127.0.0.1:8000. Press Ctrl+C to stop.'
& '.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
