@echo off
rem Doble clic para abrir el dashboard de Katapulk Fuel en el navegador.
rem Primero descarga de GitHub lo último que proceso la rutina en la nube.
rem Deja esta ventana negra abierta mientras lo uses; ciérrala para apagar el dashboard.
cd /d "%~dp0"
echo Descargando los datos mas recientes...
"C:\Program Files\Git\cmd\git.exe" pull -q --rebase origin main
echo Abriendo el dashboard de Katapulk Fuel...
echo No cierres esta ventana mientras uses el dashboard.
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:8502"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m streamlit run dashboard.py --server.port 8502 --server.headless true --browser.gatherUsageStats false
pause
