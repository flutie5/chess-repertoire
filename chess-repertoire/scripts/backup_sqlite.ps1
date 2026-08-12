# Backup Opening Explorer SQLite DB (users + jobs).
param(
  [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$DataDir = if ($env:DATA_DIR) { $env:DATA_DIR } else { Join-Path $Root "webapp" }
$Db = Join-Path $DataDir "users.db"
if (-not $OutDir) { $OutDir = Join-Path $Root "backups" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$Dest = Join-Path $OutDir "users-$Stamp.db"
$sqlite = Get-Command sqlite3 -ErrorAction SilentlyContinue
if ($sqlite) {
  & sqlite3 $Db ".backup '$Dest'"
} else {
  Copy-Item -Force $Db $Dest
  foreach ($suf in @("-wal", "-shm")) {
    $side = "$Db$suf"
    if (Test-Path $side) { Copy-Item -Force $side "$Dest$suf" }
  }
}
Write-Host "Wrote $Dest"
Get-Item "$Dest*"
