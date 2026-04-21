param(
  [string]$DatabaseUrl,
  [string]$ApiHost = "0.0.0.0",
  [int]$ApiPort = 5000,
  [string]$WorkerApiBaseUrl,
  [string]$WorkerQueueDir = "data/worker_queue",
  [string]$WorkerInternalToken,
  [string]$EnvFile,
  [switch]$Foreground,
  [switch]$ApiOnly,
  [switch]$WorkerOnly
)

$ErrorActionPreference = "Stop"

if ($ApiOnly -and $WorkerOnly) {
  throw "Choose either -ApiOnly or -WorkerOnly, not both."
}

if ($Foreground -and (-not ($ApiOnly -xor $WorkerOnly))) {
  throw "-Foreground requires exactly one of -ApiOnly or -WorkerOnly."
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$apiScript = Join-Path $PSScriptRoot "start_api.ps1"
$workerScript = Join-Path $PSScriptRoot "start_worker.ps1"

function Set-EnvFromFile {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path
  )

  if (-not (Test-Path $Path)) {
    throw "Env file not found: $Path"
  }

  Get-Content $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
      return
    }
    $eqIndex = $line.IndexOf("=")
    if ($eqIndex -lt 1) {
      return
    }
    $key = $line.Substring(0, $eqIndex).Trim()
    $value = $line.Substring($eqIndex + 1).Trim()
    if (
      ($value.StartsWith('"') -and $value.EndsWith('"')) -or
      ($value.StartsWith("'") -and $value.EndsWith("'"))
    ) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    if ($key) {
      [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
  }
}

if ($EnvFile) {
  Set-EnvFromFile -Path $EnvFile
} else {
  $defaultEnv = Join-Path $repoRoot ".env.local"
  if (Test-Path $defaultEnv) {
    Set-EnvFromFile -Path $defaultEnv
  }
}

if (-not (Test-Path $apiScript)) {
  throw "Missing script: $apiScript"
}
if (-not (Test-Path $workerScript)) {
  throw "Missing script: $workerScript"
}

if ($DatabaseUrl) {
  $env:DATABASE_URL = $DatabaseUrl
}
if (-not $env:DATABASE_URL) {
  throw "DATABASE_URL is required. Pass -DatabaseUrl or set `$env:DATABASE_URL first."
}

$env:FLASK_RUN_HOST = $ApiHost
$env:FLASK_RUN_PORT = [string]$ApiPort
$env:WORKER_QUEUE_DIR = $WorkerQueueDir

if ($WorkerApiBaseUrl) {
  $env:WORKER_API_BASE_URL = $WorkerApiBaseUrl
} elseif (-not $env:WORKER_API_BASE_URL) {
  $env:WORKER_API_BASE_URL = "http://127.0.0.1:$ApiPort"
}

if ($WorkerInternalToken) {
  $env:WORKER_INTERNAL_TOKEN = $WorkerInternalToken
}

$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)
if ($pwsh) {
  $shellExe = $pwsh.Source
  $shellArgs = @("-NoExit", "-File")
} else {
  $powershell = (Get-Command powershell -ErrorAction SilentlyContinue)
  if (-not $powershell) {
    throw "PowerShell executable was not found."
  }
  $shellExe = $powershell.Source
  $shellArgs = @("-NoExit", "-File")
}

Write-Host "Launcher configuration:" -ForegroundColor Cyan
Write-Host "- Repo root: $repoRoot"
Write-Host "- API host/port: $($env:FLASK_RUN_HOST):$($env:FLASK_RUN_PORT)"
Write-Host "- Worker API base URL: $($env:WORKER_API_BASE_URL)"
Write-Host "- Worker queue dir: $($env:WORKER_QUEUE_DIR)"

if ($Foreground -and $ApiOnly) {
  Write-Host "Running API in foreground (debug mode)..." -ForegroundColor Yellow
  & $apiScript
  exit $LASTEXITCODE
}

if ($Foreground -and $WorkerOnly) {
  Write-Host "Running worker in foreground (debug mode)..." -ForegroundColor Yellow
  & $workerScript
  exit $LASTEXITCODE
}

$launched = @()

if (-not $WorkerOnly) {
  Write-Host "Starting API window..." -ForegroundColor Green
  $apiProc = Start-Process -FilePath $shellExe -ArgumentList ($shellArgs + $apiScript) -WorkingDirectory $repoRoot -PassThru
  $launched += @{ Name = "API"; Process = $apiProc }
}

if (-not $ApiOnly) {
  Write-Host "Starting worker window..." -ForegroundColor Green
  $workerProc = Start-Process -FilePath $shellExe -ArgumentList ($shellArgs + $workerScript) -WorkingDirectory $repoRoot -PassThru
  $launched += @{ Name = "Worker"; Process = $workerProc }
}

Start-Sleep -Seconds 2
foreach ($item in $launched) {
  if ($item.Process.HasExited) {
    Write-Host "$($item.Name) process exited immediately. Use -Foreground with -ApiOnly or -WorkerOnly to see the error output." -ForegroundColor Red
  }
}

Write-Host "Done. Close API/worker windows to stop services." -ForegroundColor Cyan
