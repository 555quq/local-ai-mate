$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$start = Get-Content -LiteralPath (Join-Path $root 'deploy\windows\start-production.ps1') -Raw
$stop = Get-Content -LiteralPath (Join-Path $root 'deploy\windows\stop-production.ps1') -Raw
$install = Get-Content -LiteralPath (Join-Path $root 'deploy\windows\install-autostart.ps1') -Raw

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
        $start | Should Match 'SSH private key is missing'
        $start | Should Match 'Pinned SSH known_hosts file is missing'
        $start | Should Match 'SshPort must be between 1 and 65535'
    }

    It 'pins the SSH host and fixed loopback reverse mapping' {
        $start | Should Match 'BatchMode=yes'
        $start | Should Match 'StrictHostKeyChecking=yes'
        $start | Should Match 'UserKnownHostsFile'
        $start | Should Match 'KexAlgorithms=curve25519-sha256'
        $start | Should Match 'ExitOnForwardFailure=yes'
        $start | Should Match 'ServerAliveInterval=30'
        $start | Should Match 'ServerAliveCountMax=3'
        $start | Should Match '127\.0\.0\.1:18765:127\.0\.0\.1:8765'
        $start | Should Not Match 'frpc|FrpToken|7000'
    }

    It 'only stops processes whose command line matches the project marker' {
        $stop | Should Match 'Refusing to stop'
        $stop | Should Match 'CommandLine\.Contains\(\$marker\)'
        $stop | Should Match 'known_hosts'
        $stop | Should Match '127\.0\.0\.1:18765:127\.0\.0\.1:8765'
        $stop | Should Not Match 'frpc|7000'
    }

    It 'installs separately supervised backend and tunnel login tasks' {
        $install | Should Match 'LocalAIMate-Backend'
        $install | Should Match 'LocalAIMate-Tunnel'
        $install | Should Match 'RestartCount 999'
        $install | Should Match '-Supervise'
    }
}
