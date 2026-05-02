$ErrorActionPreference = "Stop"

if (-not $env:WORKER_ROLE) {
  $env:WORKER_ROLE = "exit"
}
if (-not $env:WORKER_STATION_ID) {
  $env:WORKER_STATION_ID = "exit-station-1"
}
if (-not $env:WORKER_CAMERA_ID) {
  $env:WORKER_CAMERA_ID = "2"
}
if (-not $env:WORKER_CCTV_STREAM_SOURCE) {
  $env:WORKER_CCTV_STREAM_SOURCE = "1"
}
if (-not $env:WORKER_API_BASE_URL) {
  $env:WORKER_API_BASE_URL = "http://127.0.0.1:5000"
}
if (-not $env:WORKER_QUEUE_DIR) {
  $env:WORKER_QUEUE_DIR = "data/worker_queue"
}

$venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
Push-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))
try {
  if (Test-Path $venvPython) {
    & $venvPython -m workers.exit_worker
  } else {
    python -m workers.exit_worker
  }
} finally {
  Pop-Location
}
