$ErrorActionPreference = "Stop"

param(
  [Parameter(Mandatory = $true)]
  [string]$OutputFile
)

if (-not $env:DATABASE_URL) {
  throw "DATABASE_URL is required."
}

$outputDir = Split-Path -Parent $OutputFile
if ($outputDir -and -not (Test-Path $outputDir)) {
  New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

pg_dump --format=custom --file="$OutputFile" "$env:DATABASE_URL"
Write-Host "Backup written to $OutputFile"
