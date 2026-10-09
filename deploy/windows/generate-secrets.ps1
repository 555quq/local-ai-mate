[CmdletBinding()]
param(
    [string]$OutputPath,
    [string]$Domain = 'luna.shipmaster.asia',
    [string]$ServerAddress = '49.234.57.214',
    [string]$SshUser = 'luna-tunnel',
    [ValidateRange(0, 65535)]
    [int]$SshPort = 0
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $OutputPath) { $OutputPath = Join-Path $ProjectRoot 'runtime\deployment\secrets.psd1' }
if (Test-Path -LiteralPath $OutputPath) {
    throw "Refusing to overwrite existing deployment secrets: $OutputPath"
}

function New-RandomUrlSafe([int]$ByteCount) {
    $bytes = New-Object byte[] $ByteCount
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

$username = "luna-$(New-RandomUrlSafe 6)".ToLowerInvariant()
$password = New-RandomUrlSafe 24
$sessionToken = New-RandomUrlSafe 48

$content = @"
@{
    Domain = '$Domain'
    ServerAddress = '$ServerAddress'
    BasicAuthUsername = '$username'
    BasicAuthPassword = '$password'
    SessionToken = '$sessionToken'
    SshUser = '$SshUser'
    SshPort = $SshPort
}
"@

$directory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $directory -Force | Out-Null
Set-Content -LiteralPath $OutputPath -Value $content -Encoding UTF8
Write-Host "Generated deployment secrets at an ignored local path. Credentials were not printed."
