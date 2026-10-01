"""Métricas del dashboard y reporte diario (Markdown). Lo usan la CLI y el dashboard."""
from datetime import date

from .alerts import all_alerts


def _one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0] or 0


def metrics(conn) -> dict:
    t = "SELECT COUNT(*) FROM iso_tanks WHERE "
    m = {
        "tanks": {
            "Total": _one(conn, "SELECT COUNT(*) FROM iso_tanks"),
            "En Cuba": _one(conn, t + "status = 'in_cuba'"),
            "Hacia Cuba": _one(conn, t + "status = 'in_transit_cuba'"),
            "Regresando": _one(conn, t + "status = 'returning'"),
            "En EE.UU.": _one(conn, t + "status IN ('available','loading','loaded','in_us_hold','ready_pickup')"),
            "Llenos": _one(conn, t + "fill_state = 'full'"),
            "Vacíos": _one(conn, t + "fill_state = 'empty'"),
            "Disponibles": _one(conn, t + "status IN ('available','ready_pickup')"),
        },
        "ops": {
            "Gasolina": _one(conn, "SELECT COUNT(*) FROM bookings WHERE fuel_type='gasoline' AND direction='outbound'"),
            "Diésel": _one(conn, "SELECT COUNT(*) FROM bookings WHERE fuel_type='diesel' AND direction='outbound'"),
            "En tránsito": _one(conn, "SELECT COUNT(*) FROM bookings WHERE status IN ('shipped','returning')"),
            "Pendientes": _one(conn, "SELECT COUNT(*) FROM bookings WHERE status IN ('active','loaded','blocked')"),
            "Completadas": _one(conn, "SELECT COUNT(*) FROM bookings WHERE status IN ('arrived','closed')"),
        },
        "money": {
            "Total pagado": _one(conn, "SELECT SUM(amount) FROM payments"),
            "Total facturado": _one(conn, "SELECT SUM(total) FROM invoices WHERE status = 'final'"),
            "Saldo pendiente": _one(conn, "SELECT SUM(balance) FROM v_invoice_status WHERE balance > 0 AND status = 'final'"),
            "Crédito con proveedores": _one(conn, "SELECT SUM(credit) FROM v_payment_status WHERE credit > 0"),
        },
        "ibc": {
            "Totes llenos": _one(conn, "SELECT SUM(tote_count) FROM ibc_lots WHERE fill_state='full'"),
            "Totes vacíos": _one(conn, "SELECT SUM(tote_count) FROM ibc_lots WHERE fill_state='empty'"),
            "En puerto": _one(conn, "SELECT SUM(tote_count) FROM ibc_lots WHERE location='port'"),
            "En tránsito": _one(conn, "SELECT SUM(tote_count) FROM ibc_lots WHERE location='in_transit'"),
            "Galones totales": _one(conn, "SELECT SUM(total_gallons) FROM ibc_lots"),
            "Contenedores 40'": _one(conn, "SELECT SUM(container_count) FROM bookings WHERE equipment='ibc'"),
        },
    }
    return m


def _fmt(k, v):
    return f"${v:,.2f}" if k in ("Total pagado", "Total facturado", "Saldo pendiente", "Crédito con proveedores") else f"{v:,}"


def daily_report(conn) -> str:
    m = metrics(conn)
    lines = [f"# Reporte diario combustible — {date.today():%Y-%m-%d}", ""]
    titles = {"tanks": "ISO Tanks", "ops": "Operaciones", "money": "Dinero", "ibc": "IBC Totes"}
    for sec, title in titles.items():
        lines.append(f"## {title}")
        lines += [f"- {k}: **{_fmt(k, v)}**" for k, v in m[sec].items()]
        lines.append("")

    alerts = all_alerts(conn)
    lines.append(f"## Acción requerida ({len(alerts)})")
    for a in alerts:
        lines.append(f"- [{a['priority']}] **{a['kind']}** — {a['ref']}: {a['message']}")
    lines.append("")

    lines.append("## Bookings abiertos")
    lines.append("| Booking | B/L | Dir | Combustible | ISO | Vessel/Voy | ETD | ETA | Cutoff | Status |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for b in conn.execute("SELECT * FROM bookings WHERE status != 'closed' ORDER BY cutoff, etd"):
        lines.append(f"| {b['booking_no']} | {b['bl_no'] or ''} | {b['direction']} | {b['fuel_type'] or b['residue_type'] or ''}"
                     f" | {b['iso_count'] or ''} | {b['vessel'] or ''} {b['voyage'] or ''} | {b['etd'] or ''}"
                     f" | {b['eta'] or ''} | {b['cutoff'] or ''} | {b['status']} |")

    lines.append("")
    n = conn.execute("SELECT COUNT(*) FROM events WHERE ts >= datetime('now','-1 day') AND action != 'set'").fetchone()[0]
    lines.append(f"## Cambios últimas 24 h ({n})" + (" — se muestran los 150 más recientes" if n > 150 else ""))
    for e in conn.execute("SELECT * FROM (SELECT * FROM events WHERE ts >= datetime('now','-1 day') AND action != 'set'"
                          " ORDER BY id DESC LIMIT 150) ORDER BY id"):
        chg = f"{e['field']}: {e['old_value']} → {e['new_value']}" if e["field"] else ""
        lines.append(f"- {e['ts']} {e['entity']} {e['entity_key']} {e['action']} {chg} ({e['source']})")
    return "\n".join(lines)
