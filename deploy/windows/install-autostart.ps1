[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$startScript = (Resolve-Path (Join-Path $PSScriptRoot 'start-production.ps1')).Path
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited

foreach ($definition in @(
    @{ Name = 'LocalAIMate-Backend'; Component = 'Backend' },
    @{ Name = 'LocalAIMate-Tunnel'; Component = 'Tunnel' }
)) {
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$startScript`" -Component $($definition.Component) -Supervise"
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
    Register-ScheduledTask -TaskName $definition.Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Write-Host "Installed scheduled task $($definition.Name)."
}
