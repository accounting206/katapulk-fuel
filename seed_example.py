"""Carga los ejemplos reales que describiste en una base de prueba (data/demo.db).

    python seed_example.py
    set KF_DB=data\\demo.db   (PowerShell: $env:KF_DB="data\\demo.db")
    python cli.py alerts
"""
import os
from pathlib import Path

os.environ.setdefault("KF_DB", str(Path(__file__).parent / "data" / "demo.db"))

from kf import ops, parsers  # noqa: E402
from kf.db import DB_PATH, connect, init_db  # noqa: E402

if DB_PATH.exists():
    DB_PATH.unlink()
conn = connect()
init_db(conn)

# --- Retornos de ISO Tanks (texto tal como llega) ---
RETORNOS = """
CAT40268984 / CWPN26189608
18 ISO Tanks
Gasolina
CAT40268986 / CWPN26189610
2 ISO Tanks
Diésel
CAT40270958 / CWPN26190387
3 ISO Tanks
Diésel
"""
for r in parsers.parse_return_lines(RETORNOS):
    ops.save_booking(conn, "import", direction="return", carrier="Crowley", pol="Mariel",
                     pod="Port Everglades", status="returning", eta="2026-10-02", **r)

# Tanques de ejemplo (números ficticios) en el primer retorno
demo_tanks = [f"KTPU{100000 + i:06d}{i % 10}" for i in range(18)]
ops.assign_tanks(conn, "CAT40268984", demo_tanks)
ops.propagate_booking_status(conn, "CAT40268984")

# --- Operación IBC Totes ---
ops.save_booking(conn, booking_no="HOU9224051A", carrier="Seaboard", equipment="ibc", fuel_type="diesel",
                 container_count=12, pol="Houston", pod="Mariel", cutoff="2026-10-01", status="active")
ops.add_ibc_lot(conn, "HOU9224051A", 300, 275, fill_state="full")
for bk, cont in [("HOU9224272A", 9), ("HOU9224301A", 9), ("HOU9224335A", 9), ("HOU9224385A", 8)]:
    ops.save_booking(conn, booking_no=bk, carrier="Seaboard", equipment="ibc", fuel_type="diesel",
                     container_count=cont, pol="Houston", pod="Mariel", cutoff="2026-10-08", status="active")
    ops.add_ibc_lot(conn, bk, cont * 20, 330)          # 700 totes de 330 en total

# --- Pago mayor que las invoices: crédito disponible ---
pid = ops.save_payment(conn, pay_date="2026-09-15", vendor="Triton Energy", amount=213418.88,
                       wire_no="WIRE-DEMO-1", concept="fuel", iso_count=20)
ops.save_invoice(conn, vendor="Triton Energy", invoice_no="T-5001", invoice_date="2026-09-18", total=98500.00)
ops.save_invoice(conn, vendor="Triton Energy", invoice_no="T-5002", invoice_date="2026-09-22", total=87250.40)
ops.auto_allocate(conn)
conn.commit()

c = conn.execute("SELECT credit FROM v_payment_status WHERE id = ?", (pid,)).fetchone()[0]
print(f"Base demo creada en {DB_PATH}")
print(f"Crédito disponible con Triton: ${c:,.2f}   (213,418.88 - 98,500.00 - 87,250.40 = 27,668.48)")
tot = conn.execute("SELECT SUM(iso_count) FROM bookings WHERE direction='return'").fetchone()[0]
print(f"ISO Tanks en retorno: {tot}")
t = conn.execute("SELECT SUM(tote_count), SUM(total_gallons) FROM ibc_lots").fetchone()
cont = conn.execute("SELECT SUM(container_count) FROM bookings WHERE equipment='ibc'").fetchone()[0]
print(f"IBC: {t[0]} totes, {t[1]:,} galones, {cont} contenedores de 40'")
