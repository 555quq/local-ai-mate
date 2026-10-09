[CmdletBinding()]
param(
    [string]$SecretsPath,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $SecretsPath) { $SecretsPath = Join-Path $ProjectRoot 'runtime\deployment\secrets.psd1' }
if (-not $OutputPath) { $OutputPath = Join-Path $ProjectRoot 'runtime\deployment\frpc.toml' }

if (-not (Test-Path -LiteralPath $SecretsPath -PathType Leaf)) {
    throw "Deployment secrets file is missing: $SecretsPath"
}

$secrets = Import-PowerShellDataFile -LiteralPath $SecretsPath
foreach ($key in 'ServerAddress', 'FrpToken') {
    if (-not $secrets[$key]) { throw "Deployment secret '$key' is missing." }
}
if ([Text.Encoding]::UTF8.GetByteCount([string]$secrets.FrpToken) -lt 32) {
    throw 'FrpToken must contain at least 32 UTF-8 bytes.'
}

$templatePath = Join-Path $PSScriptRoot 'frpc.toml.template'
$rendered = (Get-Content -LiteralPath $templatePath -Raw)
$rendered = $rendered.Replace('__SERVER_ADDR__', [string]$secrets.ServerAddress)
$rendered = $rendered.Replace('__FRP_TOKEN__', [string]$secrets.FrpToken)

$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
Set-Content -LiteralPath $OutputPath -Value $rendered -Encoding UTF8
Write-Host "Rendered FRP client configuration: $OutputPath"
