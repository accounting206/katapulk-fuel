# Rutina diaria: leer Gmail, cruzar pagos con invoices, generar reporte y alertas.
# La corre sola el Programador de tareas de Windows (tarea "Katapulk Fuel - rutina diaria").
# También se puede correr a mano:  powershell -ExecutionPolicy Bypass -File run_daily.ps1
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

New-Item -ItemType Directory -Force logs, reportes | Out-Null
$log = "logs\rutina_$(Get-Date -Format yyyy-MM-dd).log"

"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ====" | Out-File -Append -Encoding utf8 $log
& $py cli.py email --limit 500 *>&1 | Out-File -Append -Encoding utf8 $log
& $py cli.py estados           *>&1 | Out-File -Append -Encoding utf8 $log
& $py cli.py auto-apply        *>&1 | Out-File -Append -Encoding utf8 $log
& $py cli.py report --out reportes *>&1 | Out-File -Append -Encoding utf8 $log
& $py cli.py alerts > "reportes\alertas_$(Get-Date -Format yyyy-MM-dd).txt" 2>&1
& $py cli.py pagos --out reportes *>&1 | Out-Null

# Notificación de Windows con el resumen de pagos urgentes (clic -> abre el reporte)
try {
    $resumen = (& $py cli.py pagos --resumen | Out-String).Trim()
    $archivo = (Resolve-Path "reportes\pagos_urgentes_$(Get-Date -Format yyyy-MM-dd).txt").Path
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $esc = [Security.SecurityElement]::Escape($resumen)
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml("<toast activationType='protocol' launch='file:///$($archivo.Replace('\','/'))'><visual><binding template='ToastGeneric'><text>Katapulk - Pagos urgentes</text><text>$esc</text></binding></visual></toast>")
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
} catch { "no se pudo mostrar la notificación: $_" | Out-File -Append -Encoding utf8 $log }
"fin" | Out-File -Append -Encoding utf8 $log
