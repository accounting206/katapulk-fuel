#!/usr/bin/env bash
# Rutina en la nube (la ejecuta la rutina de Claude a las 9:00 y 13:00, hora de Miami).
# Requiere en el entorno de la nube: variable KF_GOOGLE_CLIENT_ID y la credencial 'Body parameter'
# para oauth2.googleapis.com/token con refresh_token y client_secret (ver README, sección Nube).
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
export TZ=America/New_York
HOY=$(date +%Y-%m-%d)

if [ -z "${KF_GOOGLE_CLIENT_ID:-}${KF_GOOGLE_TOKEN:-}" ]; then
  echo "ERROR: falta KF_GOOGLE_CLIENT_ID en las variables del entorno de la nube (ver README, sección Nube)."
  exit 2
fi

# Entorno de Python propio (.venv-nube), separado de las librerías del sistema: la 'cryptography'
# del sistema choca con la que trae pip (PanicException al importar google.auth).
if python3 -m venv .venv-nube >/dev/null 2>&1; then
  . .venv-nube/bin/activate
  python3 -m pip install -q --upgrade pip >/dev/null 2>&1 || true
  python3 -m pip install -q -r requirements-nube.txt
else
  python3 -m pip install -q -r requirements-nube.txt >/dev/null 2>&1 || python3 -m pip install -q --user -r requirements-nube.txt
  python3 -m pip install -q --force-reinstall --no-cache-dir cryptography cffi
fi
python3 -c "import google.auth.transport.requests" || { echo "ERROR: las librerías de Google no cargan en la nube."; exit 4; }

mkdir -p logs reportes
python3 cli.py email --limit 500 > "logs/email_$HOY.log" 2>&1 || { echo "ERROR leyendo Gmail:"; tail -20 "logs/email_$HOY.log"; exit 3; }
python3 cli.py estados
python3 cli.py auto-apply > /dev/null
python3 cli.py report --out reportes > /dev/null
python3 cli.py alerts > "reportes/alertas_$HOY.txt" 2>&1 || true
python3 cli.py pagos --out reportes > /dev/null

# Guardar en GitHub la base actualizada y los reportes del día
git add data/katapulk_fuel.db reportes/
if ! git diff --cached --quiet; then
  git -c user.name="Katapulk Fuel (nube)" -c user.email="accounting@katapulk.com" \
      commit -q -m "Rutina nube $HOY $(date +%H:%M)"
  git push -q origin HEAD:main
  echo "Datos guardados en GitHub."
fi

echo "=================================================="
cat "reportes/pagos_urgentes_$HOY.txt"
