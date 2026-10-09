[CmdletBinding()]
param(
    [ValidateSet('All', 'Backend', 'Tunnel')]
    [string]$Component = 'All',
    [switch]$Supervise
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$RuntimeRoot = Join-Path $ProjectRoot 'runtime\deployment'
$SecretsPath = Join-Path $RuntimeRoot 'secrets.psd1'
$PidRoot = Join-Path $RuntimeRoot 'pids'
$LogRoot = Join-Path $RuntimeRoot 'logs'
$FrpcConfig = Join-Path $RuntimeRoot 'frpc.toml'

if (-not (Test-Path -LiteralPath $SecretsPath -PathType Leaf)) {
    throw "Deployment secrets file is missing: $SecretsPath"
}
$secrets = Import-PowerShellDataFile -LiteralPath $SecretsPath
foreach ($key in 'Domain', 'SessionToken', 'FrpToken', 'ServerAddress') {
    if (-not $secrets[$key]) { throw "Deployment secret '$key' is missing." }
}

New-Item -ItemType Directory -Path $PidRoot, $LogRoot -Force | Out-Null

function Get-OwnedProcess([string]$PidFile, [string]$Marker) {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) { return $null }
    $storedPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    if ($storedPid -notmatch '^\d+$') { Remove-Item -LiteralPath $PidFile -Force; return $null }
    $record = Get-CimInstance Win32_Process -Filter "ProcessId = $storedPid" -ErrorAction SilentlyContinue
    if ($record -and $record.CommandLine -and $record.CommandLine.Contains($Marker)) {
        return Get-Process -Id ([int]$storedPid) -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $PidFile -Force
    return $null
}

function Start-OwnedProcess(
    [string]$Name,
    [string]$Executable,
    [string]$Arguments,
    [string]$WorkingDirectory,
    [string]$Marker,
    [hashtable]$Environment
) {
    $pidFile = Join-Path $PidRoot "$Name.pid"
    $existing = Get-OwnedProcess $pidFile $Marker
    if ($existing) {
        Write-Host "$Name is already running with PID $($existing.Id)."
        return $existing
    }
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { throw "$Name executable is missing: $Executable" }

    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Executable
    $info.Arguments = $Arguments
    $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($item in $Environment.GetEnumerator()) { $info.EnvironmentVariables[$item.Key] = [string]$item.Value }

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) { throw "Failed to start $Name." }
    Set-Content -LiteralPath $pidFile -Value $process.Id -Encoding ASCII
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
    Write-Host "Started $Name with PID $($process.Id)."
    return $process
}

$processes = @()
if ($Component -in @('All', 'Backend')) {
    $python = Join-Path $ProjectRoot 'backend\.venv\Scripts\python.exe'
    $backendDir = Join-Path $ProjectRoot 'backend'
    $backendEnvironment = @{
        LOCAL_AI_MATE_SESSION_TOKEN = [string]$secrets.SessionToken
        LOCAL_AI_MATE_ALLOWED_ORIGIN = "https://$($secrets.Domain)"
        LOCAL_AI_MATE_OLLAMA_ENABLED = '1'
        PYTHONUNBUFFERED = '1'
    }
    $processes += Start-OwnedProcess 'backend' $python '-m uvicorn app.main:app --host 127.0.0.1 --port 8765' $backendDir 'uvicorn app.main:app' $backendEnvironment
}

if ($Component -in @('All', 'Tunnel')) {
    & (Join-Path $PSScriptRoot 'render-config.ps1') -SecretsPath $SecretsPath -OutputPath $FrpcConfig
    $frpc = Join-Path $ProjectRoot 'runtime\frp\frpc.exe'
    $processes += Start-OwnedProcess 'frpc' $frpc "-c `"$FrpcConfig`"" $ProjectRoot $FrpcConfig @{}
}

if ($Supervise) {
    foreach ($process in $processes) {
        if ($process) { $process.WaitForExit() }
    }
}
