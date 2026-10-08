"""Actualiza la tabla de precios de venta y la imprime en el formato del WhatsApp.

- Lee precios/precios_base.csv (último precio conocido de cada proveedor/ruta/producto).
- World Fuel: toma el último correo "Katapulk Price Notice" (PDF) y usa el Total Price más bajo
  de ULSD (diésel) y de 87 E10 (gasolina).
- Los demás proveedores no mandan precio diario por correo: se quedan con su último precio y fecha.
- CIF $/L = precio $/gal / 3.78541 + costo_extra (fijo por fila, sacado de la tabla original).
  Propano: por bala de 35 lb, CIF = precio + costo_extra.  Venta = CIF + 0.40.
Uso:  python3 precios/actualizar_precios.py      (escribe precios/precios_base.csv y precios/tabla_<fecha>.txt)
"""
import base64
import csv
import io
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
BASE = AQUI / "precios_base.csv"
LITROS_GAL = 3.78541
MARGEN = 0.40


def _gmail():
    data = urllib.parse.urlencode({"client_id": os.environ["KF_GOOGLE_CLIENT_ID"], "grant_type": "refresh_token"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data), timeout=30) as r:
        tk = json.loads(r.read())["access_token"]

    def api(path, **q):
        url = "https://gmail.googleapis.com/gmail/v1/users/me/" + path + ("?" + urllib.parse.urlencode(q) if q else "")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + tk}), timeout=60) as r:
            return json.loads(r.read())
    return api


def precio_world_fuel():
    """(fecha, diesel, gasolina) del último Price Notice, o None si no hay."""
    from pypdf import PdfReader
    api = _gmail()
    msgs = api("messages", q='subject:"Katapulk Price Notice" has:attachment newer_than:7d', maxResults=1).get("messages", [])
    if not msgs:
        return None
    m = api("messages/" + msgs[0]["id"], format="full")
    pdf = next((p for p in m["payload"].get("parts", []) if p.get("mimeType") == "application/pdf"), None)
    if not pdf:
        return None
    raw = api(f"messages/{msgs[0]['id']}/attachments/{pdf['body']['attachmentId']}")["data"]
    texto = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(base64.urlsafe_b64decode(raw))).pages)
    fecha = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", texto)
    productos = re.search(r"Product\s+(.+)", texto).group(1)
    totales = [float(x) for x in re.findall(r"\$([\d.]+)", re.search(r"Total Price\s+(.+)", texto).group(1))]
    # Columnas: 3 ULSD y luego gasolinas (87 E10 x2, 89 E10). Se identifican por nombre.
    nombres = [n.strip() for n in re.split(r"(?=ULSD|8[79] E10)", productos) if n.strip()]
    if len(nombres) != len(totales):
        raise SystemExit(f"No pude leer el PDF de World Fuel (columnas {len(nombres)} vs precios {len(totales)}).")
    diesel = min(t for n, t in zip(nombres, totales) if n.startswith("ULSD"))
    gasolina = min(t for n, t in zip(nombres, totales) if n.startswith("87"))
    f = date(int(fecha.group(3)), int(fecha.group(1)), int(fecha.group(2))).isoformat()
    return f, diesel, gasolina


def main():
    filas = list(csv.DictReader(BASE.open(encoding="utf-8")))
    try:
        wf = precio_world_fuel()
    except Exception as e:  # sin World Fuel la tabla igual sale, con su último precio
        wf, aviso = None, f"(No pude leer el precio de World Fuel: {e})"
    else:
        aviso = "" if wf else "(No llegó Price Notice de World Fuel en los últimos 7 días)"
    for f in filas:
        if wf and f["proveedor"] == "W.Fuel" and f["fecha_precio"] <= wf[0]:
            f["fecha_precio"] = wf[0]
            f["precio"] = str(wf[1] if f["producto"].startswith("Di") else wf[2])
        precio, extra = float(f["precio"]), float(f["costo_extra"])
        cif = precio + extra if f["producto"].startswith("Propano") else precio / LITROS_GAL + extra
        f["cif"], f["venta"] = f"{cif:.3f}", f"{cif + MARGEN:.3f}"
        if f["producto"].startswith("Propano"):
            f["cif"], f["venta"] = f"{cif:.2f}", f"{cif + MARGEN:.2f}"

    with BASE.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0].keys()))
        w.writeheader()
        w.writerows(filas)

    hoy = datetime.now()
    lineas = [f"PRECIOS {hoy:%d/%m/%Y}", f"(Venta = CIF $/L + {MARGEN:.2f})"]
    zona = None
    for f in filas:
        if f["zona"] != zona:
            zona = f["zona"]
            lineas += ["", zona, "Prov.    Ruta      Prod.     $/gal   Fecha  CIF    Venta"]
        prod = "Propano*" if f["producto"].startswith("Propano") else f["producto"]
        precio = f"{float(f['precio']):.2f}" if prod == "Propano*" else f"{float(f['precio']):.4f}"
        fecha = datetime.fromisoformat(f["fecha_precio"]).strftime("%d/%m")
        lineas.append(f"{f['proveedor']:<8} {f['ruta']:<9} {prod:<9} {precio:<7} {fecha}  {f['cif']:<6} {f['venta']}")
    lineas += ["", "*Propano: precio, CIF y venta por BALA 35 lb", " (no por galón ni litro). CIF = bala + flete",
               " marítimo + terrestre + seguro."]
    if aviso:
        lineas += ["", aviso]
    tabla = "\n".join(lineas)
    (AQUI / f"tabla_{hoy:%Y-%m-%d}.txt").write_text(tabla + "\n", encoding="utf-8")
    print(tabla)


if __name__ == "__main__":
    main()
