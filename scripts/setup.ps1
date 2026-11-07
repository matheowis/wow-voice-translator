# One-time setup: venv + Python packages + models + llama.cpp + WoW addon.
# Usage (PowerShell):  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-Wow "D:\Gry\World of Warcraft"]
param(
    [string]$Python = "py -3.12",
    [string]$Llm = "nemotron-bolt-e4b",
    [string]$Wow = "",
    [switch]$SkipAddon
)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path "$PSScriptRoot\.."
Set-Location $Root
# keep pip cache next to the project (C: is often full)
$env:PIP_CACHE_DIR = Join-Path $Root ".cache\pip"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $found = $false
    try { Invoke-Expression "$Python --version" | Out-Null; $found = ($LASTEXITCODE -eq 0) } catch { }
    if (-not $found) {
        throw "Nie znaleziono Pythona 3.12 ($Python). Zainstaluj go z https://www.python.org/downloads/ (z 'py launcher') i uruchom skrypt ponownie."
    }
    Write-Host "== Tworze .venv"
    Invoke-Expression "$Python -m venv .venv"
}
$py = Join-Path $Root ".venv\Scripts\python.exe"
Write-Host "== Instaluje pakiety"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Instalacja pakietow nie powiodla sie - uruchom skrypt ponownie." }

Write-Host "== Pobieram modele i llama.cpp (wznawialne, z weryfikacja SHA-256)"
& $py scripts\download.py --llm $Llm
if ($LASTEXITCODE -ne 0) { throw "Pobieranie nie powiodlo sie - uruchom skrypt ponownie." }

if (-not $SkipAddon) {
    Write-Host "== Instaluje addon do WoW Forever"
    if ($Wow) { & $py -m wvt.cli install-addon --wow $Wow } else { & $py -m wvt.cli install-addon }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "!! Addon nie zostal zainstalowany. Podaj folder WoW (ten z _forever_):" -ForegroundColor Yellow
        Write-Host '   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Wow "D:\Gry\World of Warcraft"' -ForegroundColor Yellow
        exit 1
    }
}
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "== Utworzono .env - glosy postaci (opcjonalne): wpisz tam ELEVENLABS_API_KEY i uruchom raz:"
    Write-Host "   .venv\Scripts\python -m wvt.voice.design"
}
Write-Host "== Gotowe. Start: scripts\run.bat   (diagnostyka: .venv\Scripts\python -m wvt.cli check)"
