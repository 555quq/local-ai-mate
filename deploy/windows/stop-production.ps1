[CmdletBinding()]
param(
    [ValidateSet('All', 'Backend', 'Tunnel')]
    [string]$Component = 'All'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$PidRoot = Join-Path $ProjectRoot 'runtime\deployment\pids'

function Stop-OwnedProcess([string]$Name, [string]$Marker) {
    $pidFile = Join-Path $PidRoot "$Name.pid"
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return }
    $storedPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($storedPid -notmatch '^\d+$') { Remove-Item -LiteralPath $pidFile -Force; return }
    $record = Get-CimInstance Win32_Process -Filter "ProcessId = $storedPid" -ErrorAction SilentlyContinue
    if ($record -and $record.CommandLine -and $record.CommandLine.Contains($Marker)) {
        Stop-Process -Id ([int]$storedPid) -Force
        Write-Host "Stopped $Name (PID $storedPid)."
    } elseif ($record) {
        throw "Refusing to stop PID $storedPid because it is not the recorded $Name process."
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

if ($Component -in @('All', 'Tunnel')) {
    Stop-OwnedProcess 'frpc' (Join-Path $ProjectRoot 'runtime\deployment\frpc.toml')
}
if ($Component -in @('All', 'Backend')) {
    Stop-OwnedProcess 'backend' 'uvicorn app.main:app'
}
