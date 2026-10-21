# Crea (o reemplaza) la tarea programada "Monitor cortes SEC":
#   - cada hora y al iniciar sesion
#   - si el equipo estaba apagado a la hora prevista, corre apenas se enciende
#   - solo con la sesion de usuario abierta (no guarda contrasenas)
# Quitar:  Unregister-ScheduledTask -TaskName "Monitor cortes SEC" -Confirm:$false
$ErrorActionPreference = "Stop"
$nombre = "Monitor cortes SEC"
$py = (Get-Command python).Source
$script = Join-Path $PSScriptRoot "run_local.ps1"

$accion = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $PSScriptRoot `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Python `"$py`""

$inicio = (Get-Date).Date.AddHours((Get-Date).Hour + 1).AddMinutes(10)   # proxima hora, minuto 10
$cadaHora = New-ScheduledTaskTrigger -Once -At $inicio `
    -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$alLogin = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $nombre -Action $accion -Trigger @($cadaHora, $alLogin) `
    -Settings $ajustes -Principal $principal -Force | Out-Null
Get-ScheduledTask -TaskName $nombre | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime
Write-Host "Tarea '$nombre' instalada. Python: $py"
