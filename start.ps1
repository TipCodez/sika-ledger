$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (!(Test-Path '.venv\Scripts\python.exe')) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
}
$dependencyHash = (Get-FileHash 'requirements.txt' -Algorithm SHA256).Hash + (Get-FileHash 'requirements-dev.txt' -Algorithm SHA256).Hash
$stampPath = '.venv\sika-requirements.hash'
$previousHash = if (Test-Path $stampPath) { (Get-Content $stampPath -Raw).Trim() } else { '' }
if ($previousHash -ne $dependencyHash) {
    & '.venv\Scripts\python.exe' -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    Set-Content -Path $stampPath -Value $dependencyHash -Encoding ASCII
}
if (!(Test-Path '.env')) { Copy-Item '.env.example' '.env' }
Write-Host 'Starting Sika Ledger using the local port in PUBLIC_BASE_URL. Press Ctrl+C to stop.'
& '.venv\Scripts\python.exe' dev_server.py
