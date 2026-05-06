param(
    [string]$PythonVersion = "3.10",
    [string]$PostgresPackageId = "PostgreSQL.PostgreSQL",
    [string]$VenvPath = ".venv"
)

$ErrorActionPreference = "Stop"

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Install-WithWinget([string]$PackageId) {
    Write-Host "Installing $PackageId via winget..."
    winget install --id $PackageId --source winget --accept-package-agreements --accept-source-agreements --silent
}

function Invoke-And-Check {
    param([string]$CommandName, [scriptblock]$Action)
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$CommandName failed with exit code $LASTEXITCODE"
    }
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
Set-Location $RepoRoot

Require-Command winget

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Install-WithWinget "Python.Python.$PythonVersion"
}

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
    Install-WithWinget $PostgresPackageId
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

python -m venv $VenvPath
$PythonExe = Join-Path $RepoRoot "$VenvPath\Scripts\python.exe"

Invoke-And-Check "pip bootstrap" { & $PythonExe -m pip install --upgrade pip wheel setuptools }
Invoke-And-Check "pip requirements install" { & $PythonExe -m pip install -r requirements.txt }

Write-Host "Running first-run setup..."
Invoke-And-Check "first-run setup" { & $PythonExe scripts\first_run_setup.py }

Write-Host "Starting Library Face Access System launcher..."
Invoke-And-Check "library face access system launcher" { & $PythonExe -m app.windows_launcher }
