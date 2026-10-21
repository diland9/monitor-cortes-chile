# Crea (o reemplaza) la tarea programada "Monitor cortes SEC". Se dispara:
#   - cada hora (minuto 10)
#   - al iniciar sesion
#   - al desbloquear el equipo
#   - al despertar de suspension / hibernacion (con 40 s de espera para que vuelva la red)
# En cada disparo run_local.ps1 revisa si la ultima actualizacion fue hace menos de 55 min
# (no hace nada) o mas (actualiza y recupera las horas perdidas, hasta 7 dias).
# Solo corre con la sesion de usuario abierta (no guarda contrasenas).
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

# al despertar: evento 1 de Power-Troubleshooter (el equipo salio de suspension)
$cEvento = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$alDespertar = New-CimInstance -CimClass $cEvento -ClientOnly
$alDespertar.Enabled = $true
$alDespertar.Delay = "PT40S"
$alDespertar.Subscription = "<QueryList><Query Id=`"0`" Path=`"System`"><Select Path=`"System`">*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]</Select></Query></QueryList>"

# al desbloquear la sesion
$cSesion = Get-CimClass -ClassName MSFT_TaskSessionStateChangeTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$alDesbloquear = New-CimInstance -CimClass $cSesion -ClientOnly
$alDesbloquear.StateChange = 8        # TASK_SESSION_UNLOCK
$alDesbloquear.UserId = "$env:USERDOMAIN\$env:USERNAME"
$alDesbloquear.Enabled = $true
$alDesbloquear.Delay = "PT10S"

$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $nombre -Action $accion -Trigger @($cadaHora, $alLogin, $alDespertar, $alDesbloquear) `
    -Settings $ajustes -Principal $principal -Force | Out-Null
Get-ScheduledTask -TaskName $nombre | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime
Write-Host "Tarea '$nombre' instalada con $((Get-ScheduledTask -TaskName $nombre).Triggers.Count) disparadores. Python: $py"
