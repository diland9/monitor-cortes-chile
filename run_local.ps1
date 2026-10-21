# Captura la SEC desde este equipo (apps.sec.cl bloquea la nube), guarda el historial
# y lo sube a GitHub. Lo lanza la tarea programada "Monitor cortes SEC" (instalar_tarea.ps1).
param([string]$Python = "python")

$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = Join-Path $PSScriptRoot "logs\local.log"
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 1MB)) { Remove-Item $log }

function Log($m) { "$(Get-Date -Format 's') $m" | Add-Content -Encoding utf8 $log }
function Run($label, [scriptblock]$cmd) {
    & $cmd 2>&1 | ForEach-Object { Log "${label}: $_" }
    return $LASTEXITCODE
}

Log "inicio"
$env:PYTHONUTF8 = "1"

if ((Run "git-pull" { git pull --rebase --autostash origin main }) -ne 0) { Log "pull fallo; sigo igual" }

if ((Run "scraper" { & $Python sec_scraper.py }) -ne 0) { Log "ERROR: el scraper fallo"; exit 1 }

git add data docs/data.json docs/mapa.json
git diff --staged --quiet
if ($LASTEXITCODE -ne 0) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm")
    Run "git-commit" { git -c user.name=diland9 -c user.email=diland9@users.noreply.github.com commit -m "sec $stamp (equipo local)" } | Out-Null
    if ((Run "git-push" { git push origin main }) -ne 0) {
        # el bot de empresas pudo adelantarse: reintenta una vez
        Run "git-pull" { git pull --rebase --autostash origin main } | Out-Null
        if ((Run "git-push" { git push origin main }) -ne 0) { Log "ERROR: push fallo"; exit 1 }
    }
    Log "subido a GitHub"
} else {
    Log "sin cambios"
}
Log "fin"
