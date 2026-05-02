$ErrorActionPreference = "Stop"

if (-not $env:WORKER_ROLE) {
  $env:WORKER_ROLE = "entry"
}
if (-not $env:WORKER_STATION_ID) {
  $env:WORKER_STATION_ID = "entry-station-1"
}
if (-not $env:WORKER_CAMERA_ID) {
  $env:WORKER_CAMERA_ID = "1"
}
if (-not $env:WORKER_CCTV_STREAM_SOURCE) {
  $env:WORKER_CCTV_STREAM_SOURCE = "0"
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
    & $venvPython -m workers.entry_worker
  } else {
    python -m workers.entry_worker
  }
} finally {
  Pop-Location
}
