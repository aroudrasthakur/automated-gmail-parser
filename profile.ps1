# Dot-source once in this folder:  . .\profile.ps1
# Then type:  start

$Root = $PSScriptRoot
function global:start {
  $py = Join-Path $Root "venv\Scripts\python.exe"
  $app = Join-Path $Root "app.py"
  if (-not (Test-Path $py)) {
    Write-Error "Virtual environment not found. Run: python -m venv venv"
    return
  }
  Set-Location $Root
  & $py $app
}

Write-Host "Gmail parser ready — type start to run the app." -ForegroundColor Cyan
