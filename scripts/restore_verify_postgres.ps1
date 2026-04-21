$ErrorActionPreference = "Stop"

param(
  [Parameter(Mandatory = $true)]
  [string]$BackupFile,
  [Parameter(Mandatory = $true)]
  [string]$RestoreUrl
)

if (-not (Test-Path $BackupFile)) {
  throw "Backup file not found: $BackupFile"
}

pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$RestoreUrl" "$BackupFile"

$query = "SELECT (SELECT COUNT(*) FROM users) AS users_count, (SELECT COUNT(*) FROM recognition_events) AS events_count;"
psql "$RestoreUrl" -c "$query"
