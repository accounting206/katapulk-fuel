#!/usr/bin/env bash
# Tabla de precios de venta (la ejecuta la rutina de correos a las 10am, hora de Miami).
# Actualiza precios/precios_base.csv con el último Price Notice de World Fuel, imprime la tabla
# y guarda los cambios en GitHub (main).
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
export TZ=America/New_York

if python3 -m venv .venv-nube >/dev/null 2>&1; then
  . .venv-nube/bin/activate
fi
python3 -c "import pypdf" 2>/dev/null || python3 -m pip install -q "pypdf>=4.0" >/dev/null 2>&1

python3 precios/actualizar_precios.py

git add precios/
if ! git diff --cached --quiet; then
  git -c user.name="Katapulk Fuel (nube)" -c user.email="accounting@katapulk.com" \
      commit -q -m "Precios $(date +%Y-%m-%d)"
  git pull -q --rebase origin main && git push -q origin HEAD:main || echo "(No se pudo guardar la tabla en GitHub)"
fi
