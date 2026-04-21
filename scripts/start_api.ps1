$ErrorActionPreference = "Stop"

if (-not $env:DATABASE_URL) {
  throw "DATABASE_URL is required and must target PostgreSQL."
}

if (-not $env:FLASK_RUN_HOST) {
  $env:FLASK_RUN_HOST = "0.0.0.0"
}
if (-not $env:FLASK_RUN_PORT) {
  $env:FLASK_RUN_PORT = "5000"
}

$venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (Test-Path $venvPython) {
  & $venvPython (Join-Path $PSScriptRoot "..\app.py")
} else {
  python (Join-Path $PSScriptRoot "..\app.py")
}
