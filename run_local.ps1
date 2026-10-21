# Captura la SEC desde este equipo (apps.sec.cl bloquea la nube), guarda el historial
# y lo sube a GitHub. Lo lanza la tarea programada "Monitor cortes SEC" (instalar_tarea.ps1)
# cada hora, al iniciar sesion, al desbloquear el equipo y al despertar de suspension.
#
# Revision automatica: si la ultima actualizacion exitosa fue hace menos de 55 minutos
# no hace nada; si fue hace mas, actualiza (y recupera las horas perdidas, hasta 7 dias).
#   -Forzar  ignora esa revision y actualiza igual.
param([string]$Python = "python", [switch]$Forzar)

$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = Join-Path $PSScriptRoot "logs\local.log"
$okFile = Join-Path $PSScriptRoot "logs\last_ok.txt"
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 1MB)) { Remove-Item $log }

function Log($m) { "$(Get-Date -Format 's') $m" | Add-Content -Encoding utf8 $log }
function Run($label, [scriptblock]$cmd) {
    & $cmd 2>&1 | ForEach-Object { Log "${label}: $_" }
    return $LASTEXITCODE
}

# ---- 1) revision: esta al dia?
if (-not $Forzar -and (Test-Path $okFile)) {
    try {
        $ultima = [datetime]::Parse((Get-Content $okFile -Raw).Trim())
        $min = ((Get-Date) - $ultima).TotalMinutes
        if ($min -ge 0 -and $min -lt 55) { Log ("al dia: ultima actualizacion hace {0:N0} min; nada que hacer" -f $min); exit 0 }
        Log ("desactualizado: ultima actualizacion hace {0:N0} min; actualizando" -f $min)
    } catch { Log "no pude leer last_ok.txt; actualizo igual" }
} else {
    Log "inicio (forzado o primera vez)"
}

# ---- 2) espera conexion con la SEC (al despertar la red tarda unos segundos)
$conectado = $false
for ($i = 0; $i -lt 24 -and -not $conectado; $i++) {
    try { Invoke-WebRequest -UseBasicParsing -Uri "https://apps.sec.cl/INTONLINEv1/index.aspx" -TimeoutSec 8 | Out-Null; $conectado = $true }
    catch { Start-Sleep -Seconds 5 }
}
if (-not $conectado) { Log "sin conexion con la SEC (2 min); se reintentara en el proximo disparo"; exit 1 }

$env:PYTHONUTF8 = "1"

# ---- 3) captura, commit y push
if ((Run "git-pull" { git pull --rebase --autostash origin main }) -ne 0) { Log "pull fallo; sigo igual" }

if ((Run "scraper" { & $Python sec_scraper.py }) -ne 0) { Log "ERROR: el scraper fallo"; exit 1 }

git add data docs/data.json docs/mapa.json
git diff --staged --quiet
if ($LASTEXITCODE -ne 0) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm")
    Run "git-commit" { git -c user.name=diland9 -c user.email=diland9@users.noreply.github.com commit -m "sec $stamp (equipo local)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" } | Out-Null
    if ((Run "git-push" { git push origin main }) -ne 0) {
        # el bot de empresas pudo adelantarse: reintenta una vez
        Run "git-pull" { git pull --rebase --autostash origin main } | Out-Null
        if ((Run "git-push" { git push origin main }) -ne 0) { Log "ERROR: push fallo"; exit 1 }
    }
    Log "subido a GitHub"
} else {
    Log "sin cambios"
}
(Get-Date).ToString("s") | Set-Content -Encoding ascii $okFile
Log "fin (ok)"
