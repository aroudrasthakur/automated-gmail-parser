$Root = $PSScriptRoot
$Py = Join-Path $Root "venv\Scripts\python.exe"
$App = Join-Path $Root "app.py"
if (-not (Test-Path $Py)) {
  Write-Error "Virtual environment not found. Run: python -m venv venv"
  exit 1
}
Set-Location $Root
& $Py $App
