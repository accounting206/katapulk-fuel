"""Datos que no salen de los emails y se cargan a mano (bookings conocidos, lotes de IBC...).

Lo usa `python cli.py rebuild` para reconstruir la base desde cero. Edita aquí y vuelve a correr rebuild.
"""
from kf import ops

RETORNOS = [  # booking, B/L, tanques, residuo
    ("CAT40268984", "CWPN26189608", 18, "gasoline"),
    ("CAT40268986", "CWPN26189610", 2, "diesel"),
    ("CAT40270958", "CWPN26190387", 3, "diesel"),
]

IBC = [  # booking, contenedores 40', totes, galones por tote (None = aún no se sabe)
    ("HOU9224051A", 12, 300, 275),
    ("HOU9224272A", None, None, 330),
    ("HOU9224301A", None, None, 330),
    ("HOU9224335A", None, None, 330),
    ("HOU9224385A", None, None, 330),
]


def load(conn):
    src = "datos_iniciales"
    for bk, bl, n, fuel in RETORNOS:
        ops.save_booking(conn, src, booking_no=bk, direction="return", carrier="Crowley", residue_type=fuel,
                         iso_count=n, bl_no=bl, vessel="QUETZAL", voyage="CUB6124", pod="Port Everglades",
                         status="returning")
    for bk, cont, totes, cap in IBC:
        ops.save_booking(conn, src, booking_no=bk, carrier="Seaboard", equipment="ibc", fuel_type="diesel",
                         container_count=cont, pol="Houston", pod="Mariel", status="active")
        if totes:
            ops.add_ibc_lot(conn, bk, totes, cap, src, vendor="MidTex", fill_state="empty")
