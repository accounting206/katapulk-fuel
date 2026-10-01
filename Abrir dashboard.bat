@echo off
rem Doble clic para abrir el dashboard de Katapulk Fuel en el navegador.
rem Deja esta ventana negra abierta mientras lo uses; ciérrala para apagar el dashboard.
cd /d "%~dp0"
echo Abriendo el dashboard de Katapulk Fuel...
echo No cierres esta ventana mientras uses el dashboard.
rem abre el navegador unos segundos despues, cuando el dashboard ya esta listo
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:8502"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m streamlit run dashboard.py --server.port 8502 --server.headless true --browser.gatherUsageStats false
pause
