[CmdletBinding()]
param(
    [ValidateSet('All', 'Backend', 'Tunnel')]
    [string]$Component = 'All'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$PidRoot = Join-Path $ProjectRoot 'runtime\deployment\pids'

function Stop-OwnedProcess([string]$Name, [string[]]$Markers) {
    $pidFile = Join-Path $PidRoot "$Name.pid"
    if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) { return }
    $storedPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($storedPid -notmatch '^\d+$') { Remove-Item -LiteralPath $pidFile -Force; return }
    $record = Get-CimInstance Win32_Process -Filter "ProcessId = $storedPid" -ErrorAction SilentlyContinue
    $matches = $record -and $record.CommandLine
    foreach ($marker in $Markers) { $matches = $matches -and $record.CommandLine.Contains($marker) }
    if ($matches) {
        Stop-Process -Id ([int]$storedPid) -Force
        Write-Host "Stopped $Name (PID $storedPid)."
    } elseif ($record) {
        throw "Refusing to stop PID $storedPid because it is not the recorded $Name process."
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

if ($Component -in @('All', 'Tunnel')) {
    Stop-OwnedProcess 'ssh-tunnel' @(
        (Join-Path $ProjectRoot 'runtime\deployment\ssh\known_hosts'),
        '127.0.0.1:18765:127.0.0.1:8765'
    )
}
if ($Component -in @('All', 'Backend')) {
    Stop-OwnedProcess 'backend' @('uvicorn app.main:app', '--host 127.0.0.1', '--port 8765')
}
