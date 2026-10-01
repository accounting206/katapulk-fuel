#!/usr/bin/env bash
# Rutina en la nube (la ejecuta la rutina de Claude a las 7:00 y 13:00, hora de Miami).
# Requiere la variable de entorno KF_GOOGLE_TOKEN (contenido de token.json) configurada en el entorno de la nube.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
export TZ=America/New_York
HOY=$(date +%Y-%m-%d)

if [ -z "${KF_GOOGLE_TOKEN:-}" ]; then
  echo "ERROR: falta KF_GOOGLE_TOKEN en el entorno de la nube (ver README, sección Nube)."
  exit 2
fi

python3 -m pip install -q -r requirements-nube.txt >/dev/null 2>&1 || python3 -m pip install -q --user -r requirements-nube.txt

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
