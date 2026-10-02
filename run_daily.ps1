# Laptop: SOLO sincroniza y avisa. El trabajo (leer Gmail, actualizar la base, pagos urgentes) lo hace
# la rutina de Claude en la nube a las 7:00 y 13:00 y lo guarda en GitHub.
# Esta tarea (Programador de tareas "Katapulk Fuel - rutina diaria") descarga lo último y muestra
# la notificación de pagos urgentes en Windows.
# Para procesar en la laptop como antes (por ejemplo, si la nube falla):  run_daily.ps1 -Local
param([switch]$Local)
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$git = "C:\Program Files\Git\cmd\git.exe"
New-Item -ItemType Directory -Force logs, reportes | Out-Null
$log = "logs\rutina_$(Get-Date -Format yyyy-MM-dd).log"
"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ====" | Out-File -Append -Encoding utf8 $log

# 1) Traer lo que hizo la nube. Cambios locales sin guardar en la base son solo "toques" (la nube manda);
#    los pagos manuales ya quedaron guardados en un commit, así que no se pierden.
& $git checkout -- data/katapulk_fuel.db reportes *>&1 | Out-Null
& $git pull -q --rebase origin main *>&1 | Out-File -Append -Encoding utf8 $log

if ($Local) {
    & $py cli.py email --limit 500 *>&1 | Out-File -Append -Encoding utf8 $log
    & $py cli.py estados           *>&1 | Out-File -Append -Encoding utf8 $log
    & $py cli.py auto-apply        *>&1 | Out-File -Append -Encoding utf8 $log
    & $py cli.py report --out reportes *>&1 | Out-File -Append -Encoding utf8 $log
    & $py cli.py pagos --out reportes *>&1 | Out-Null
    & $git add data/katapulk_fuel.db reportes
    & $git commit -q -m "Rutina laptop $(Get-Date -Format 'yyyy-MM-dd HH:mm')" *>&1 | Out-Null
    & $git push -q origin main *>&1 | Out-File -Append -Encoding utf8 $log
}

# 2) Notificación de Windows con el resumen de pagos urgentes (clic -> abre el reporte)
try {
    $resumen = (& $py cli.py pagos --resumen | Out-String).Trim()
    $hoy = "reportes\pagos_urgentes_$(Get-Date -Format yyyy-MM-dd).txt"
    if (-not (Test-Path $hoy)) { & $py cli.py pagos --out reportes | Out-Null }
    $archivo = (Resolve-Path $hoy).Path
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $esc = [Security.SecurityElement]::Escape($resumen)
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml("<toast activationType='protocol' launch='file:///$($archivo.Replace('\','/'))'><visual><binding template='ToastGeneric'><text>Katapulk - Pagos urgentes</text><text>$esc</text></binding></visual></toast>")
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
} catch { "no se pudo mostrar la notificación: $_" | Out-File -Append -Encoding utf8 $log }
"fin" | Out-File -Append -Encoding utf8 $log
