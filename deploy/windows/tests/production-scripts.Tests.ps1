$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$start = Get-Content -LiteralPath (Join-Path $root 'deploy\windows\start-production.ps1') -Raw
$stop = Get-Content -LiteralPath (Join-Path $root 'deploy\windows\stop-production.ps1') -Raw
$install = Get-Content -LiteralPath (Join-Path $root 'deploy\windows\install-autostart.ps1') -Raw
$render = Get-Content -LiteralPath (Join-Path $root 'deploy\windows\render-config.ps1') -Raw

Describe 'Local AI Mate production lifecycle scripts' {
    It 'binds Uvicorn to loopback and uses the production origin' {
        $start | Should Match '--host 127\.0\.0\.1 --port 8765'
        $start | Should Match 'LOCAL_AI_MATE_ALLOWED_ORIGIN'
        $start | Should Match 'https://\$\(\$secrets\.Domain\)'
    }

    It 'requires ignored secrets and protects against duplicate processes' {
        $start | Should Match 'secrets\.psd1'
        $start | Should Match 'Get-OwnedProcess'
        $start | Should Match 'is already running'
        $render | Should Match 'at least 32 UTF-8 bytes'
    }

    It 'only stops processes whose command line matches the project marker' {
        $stop | Should Match 'Refusing to stop'
        $stop | Should Match 'CommandLine\.Contains\(\$Marker\)'
    }

    It 'installs separately supervised backend and tunnel login tasks' {
        $install | Should Match 'LocalAIMate-Backend'
        $install | Should Match 'LocalAIMate-Tunnel'
        $install | Should Match 'RestartCount 999'
        $install | Should Match '-Supervise'
    }
}
