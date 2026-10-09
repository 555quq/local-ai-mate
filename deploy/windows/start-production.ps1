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
$SshRoot = Join-Path $RuntimeRoot 'ssh'
$PrivateKey = Join-Path $SshRoot 'id_ed25519'
$KnownHosts = Join-Path $SshRoot 'known_hosts'

if (-not (Test-Path -LiteralPath $SecretsPath -PathType Leaf)) {
    throw "Deployment secrets file is missing: $SecretsPath"
}
$secrets = Import-PowerShellDataFile -LiteralPath $SecretsPath
foreach ($key in 'Domain', 'SessionToken') {
    if (-not $secrets[$key]) { throw "Deployment secret '$key' is missing." }
}
if ($Component -in @('All', 'Tunnel')) {
    foreach ($key in 'ServerAddress', 'SshUser', 'SshPort') {
        if (-not $secrets[$key]) { throw "Deployment SSH setting '$key' is missing." }
    }
    if ([int]$secrets.SshPort -lt 1 -or [int]$secrets.SshPort -gt 65535) {
        throw 'SshPort must be between 1 and 65535.'
    }
    if (-not (Test-Path -LiteralPath $PrivateKey -PathType Leaf)) { throw "SSH private key is missing: $PrivateKey" }
    if (-not (Test-Path -LiteralPath $KnownHosts -PathType Leaf)) { throw "Pinned SSH known_hosts file is missing: $KnownHosts" }
}

New-Item -ItemType Directory -Path $PidRoot, $LogRoot -Force | Out-Null

function Get-OwnedProcess([string]$PidFile, [string[]]$Markers) {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) { return $null }
    $storedPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    if ($storedPid -notmatch '^\d+$') { Remove-Item -LiteralPath $PidFile -Force; return $null }
    $record = Get-CimInstance Win32_Process -Filter "ProcessId = $storedPid" -ErrorAction SilentlyContinue
    $matches = $record -and $record.CommandLine
    foreach ($marker in $Markers) { $matches = $matches -and $record.CommandLine.Contains($marker) }
    if ($matches) {
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
    [string[]]$Markers,
    [hashtable]$Environment
) {
    $pidFile = Join-Path $PidRoot "$Name.pid"
    $existing = Get-OwnedProcess $pidFile $Markers
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
    $processes += Start-OwnedProcess 'backend' $python '-m uvicorn app.main:app --host 127.0.0.1 --port 8765' $backendDir @('uvicorn app.main:app', '--host 127.0.0.1', '--port 8765') $backendEnvironment
}

if ($Component -in @('All', 'Tunnel')) {
    $ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    $reverseMap = '127.0.0.1:18765:127.0.0.1:8765'
    $destination = "$($secrets.SshUser)@$($secrets.ServerAddress)"
    $sshArguments = @(
        '-NT',
        '-o BatchMode=yes',
        '-o StrictHostKeyChecking=yes',
        "-o UserKnownHostsFile=`"$KnownHosts`"",
        '-o KexAlgorithms=curve25519-sha256',
        '-o ExitOnForwardFailure=yes',
        '-o ServerAliveInterval=30',
        '-o ServerAliveCountMax=3',
        "-i `"$PrivateKey`"",
        "-p $([int]$secrets.SshPort)",
        "-R $reverseMap",
        $destination
    ) -join ' '
    $processes += Start-OwnedProcess 'ssh-tunnel' $ssh $sshArguments $ProjectRoot @($KnownHosts, $reverseMap, $destination) @{}
}

if ($Supervise) {
    foreach ($process in $processes) {
        if ($process) { $process.WaitForExit() }
    }
    # A supervised production component is expected to remain alive. Any exit
    # asks Task Scheduler to apply its configured restart policy.
    exit 1
}
