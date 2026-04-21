$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

if (-not $env:WORKER_API_BASE_URL) {
  $env:WORKER_API_BASE_URL = "http://127.0.0.1:5000"
}
if (-not $env:WORKER_QUEUE_DIR) {
  $env:WORKER_QUEUE_DIR = "data/worker_queue"
}

$venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
Push-Location $repoRoot
try {
  if (Test-Path $venvPython) {
    & $venvPython -m workers.recognition_worker
  } else {
    python -m workers.recognition_worker
  }
} finally {
  Pop-Location
}
