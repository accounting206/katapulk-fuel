"""Reglas de alerta. Cada regla es una función pequeña: agregar una nueva = escribir otra función
y ponerla en RULES. Devuelven dicts {priority, kind, ref, message}."""
import re
from datetime import date, datetime, timedelta

CUTOFF_DAYS = 3          # avisar cuando el cutoff está a <= N días
ARRIVAL_DAYS = 5         # avisar tanques que llegan a EE.UU. en <= N días


def _d(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:10]).date()
    except ValueError:
        return None


def _a(priority, kind, ref, message):
    return {"priority": priority, "kind": kind, "ref": ref, "message": message}


OPEN = "status NOT IN ('closed')"


def blocked_bookings(conn):
    for b in conn.execute(f"SELECT booking_no, notes FROM bookings WHERE status = 'blocked'"):
        yield _a("ALTA", "Booking bloqueado", b["booking_no"], b["notes"] or "")


def missing_docs(conn):
    for b in conn.execute(f"SELECT * FROM bookings WHERE {OPEN} AND direction='outbound'"
                          " AND (docs_complete = 0 OR hazmat_approved = 0) AND status IN ('active','loaded')"):
        falta = [n for n, v in (("documentos", b["docs_complete"]), ("IMO/Hazmat", b["hazmat_approved"])) if not v]
        yield _a("ALTA", "Documentos faltantes", b["booking_no"], "Falta: " + ", ".join(falta))


def cutoff_soon(conn, today=None):
    today = today or date.today()
    for b in conn.execute(f"SELECT booking_no, cutoff, vessel, voyage FROM bookings WHERE status IN ('active','loaded')"):
        d = _d(b["cutoff"])
        if d and d - today <= timedelta(days=CUTOFF_DAYS):
            dias = (d - today).days
            txt = "VENCIDO" if dias < 0 else f"en {dias} día(s)"
            yield _a("ALTA", "Cutoff próximo", b["booking_no"], f"Cutoff {b['cutoff']} ({txt}) {b['vessel'] or ''} {b['voyage'] or ''}")


def unpaid_bookings(conn):
    for b in conn.execute(f"SELECT booking_no FROM bookings b WHERE {OPEN} AND paid = 0 AND status != 'active'"
                          " AND NOT EXISTS (SELECT 1 FROM v_booking_money m WHERE m.booking_no = b.booking_no"
                          " AND m.paid > 0)"):
        yield _a("ALTA", "Pago pendiente", b["booking_no"], "Booking en movimiento sin marcar como pagado")


def open_invoices(conn):
    """Una línea por proveedor (con el detalle de invoices), para que 30 invoices de demurrage no tapen lo demás."""
    rows = conn.execute("SELECT * FROM v_invoice_status WHERE balance > 0.009 AND status = 'final'"
                        " ORDER BY vendor, invoice_date").fetchall()
    by_vendor = {}
    for i in rows:
        by_vendor.setdefault(i["vendor"], []).append(i)
    for vendor, invs in by_vendor.items():
        total = sum(i["balance"] for i in invs)
        detail = ", ".join(f"#{i['invoice_no']} ${i['balance']:,.0f}" + (f" ({i['booking_no']})" if i["booking_no"] else "")
                           for i in invs[:6]) + (f" … y {len(invs) - 6} más" if len(invs) > 6 else "")
        yield _a("ALTA", "Invoices con saldo", f"{vendor} ({len(invs)})", f"Saldo ${total:,.2f}: {detail}")


def provisional_invoices(conn):
    for i in conn.execute("SELECT * FROM invoices WHERE status = 'provisional'"):
        yield _a("MEDIA", "Invoice provisional", f"{i['vendor']} #{i['invoice_no']}",
                 f"${i['total']:,.2f} — {i['notes'] or ''} (no cuenta en saldos hasta la invoice final)")


def new_invoices(conn):
    for i in conn.execute("SELECT * FROM invoices WHERE invoice_date >= date('now','-2 day')"):
        yield _a("ALTA", "Invoice nueva", f"{i['vendor']} #{i['invoice_no']}", f"${i['total']:,.2f}")


def payment_credit(conn):
    for p in conn.execute("SELECT * FROM v_payment_status WHERE credit > 0.009"):
        yield _a("ALTA", "Pago con crédito disponible", f"{p['vendor']} wire {p['wire_no'] or p['id']}",
                 f"Pagado ${p['amount']:,.2f}, aplicado ${p['applied']:,.2f}, crédito ${p['credit']:,.2f}")


def invoice_vs_booking_payments(conn):
    """Lo pagado a un booking (según el reparto del pago) no coincide con lo facturado para ese booking."""
    for r in conn.execute("SELECT * FROM v_booking_money WHERE paid > 0 AND invoiced > 0 AND ABS(diff) > 0.009"):
        estado = "pagamos de más" if r["diff"] > 0 else "falta pagar"
        yield _a("ALTA", "Factura no coincide con pago", f"{r['vendor']} / {r['booking_no']}",
                 f"Facturado ${r['invoiced']:,.2f} vs pagado ${r['paid']:,.2f} → {estado} ${abs(r['diff']):,.2f}")


def paid_without_invoice(conn):
    for r in conn.execute("SELECT * FROM v_booking_money WHERE paid > 0 AND invoiced = 0"):
        yield _a("MEDIA", "Pagado, falta invoice", f"{r['vendor']} / {r['booking_no']}",
                 f"Pagado ${r['paid']:,.2f}, todavía no llegó la invoice")


def tanks_arriving(conn, today=None):
    today = today or date.today()
    for b in conn.execute("SELECT * FROM bookings WHERE direction='return' AND status IN ('active','shipped','returning')"):
        d = _d(b["eta"])
        if d and d - today <= timedelta(days=ARRIVAL_DAYS):
            yield _a("ALTA", "Tanques llegando a EE.UU.", b["booking_no"],
                     f"{b['iso_count']} ISO ETA {b['eta']} — A/N:{'sí' if b['arrival_notice'] else 'NO'}"
                     f" customs:{'sí' if b['customs_cleared'] else 'NO'} broker:{'sí' if b['brokerage_done'] else 'NO'}")


def tanks_ready_pickup(conn):
    for r in conn.execute("SELECT location, COUNT(*) n, GROUP_CONCAT(tank_no, ' ') tanks FROM iso_tanks"
                          " WHERE status = 'ready_pickup' GROUP BY location"):
        yield _a("ALTA", "Tanques disponibles para pickup", r["location"] or "¿?", f"{r['n']} tanques: {r['tanks']}")


def tanks_without_next(conn):
    rows = conn.execute("SELECT tank_no FROM iso_tanks WHERE status IN ('in_us_hold','ready_pickup','available')"
                        " AND next_booking IS NULL").fetchall()
    if rows:
        yield _a("ALTA", "ISO Tanks sin próximo booking", f"{len(rows)} tanques",
                 " ".join(r["tank_no"] for r in rows))


ACTION_CATEGORIES = ("invoice", "payment_request", "doc_problem", "arrival_notice", "release", "booking_change")


def unreviewed_emails(conn):
    """Solo emails de los últimos 7 días con algo accionable que el sistema no pudo aplicar solo."""
    rows = conn.execute("SELECT received_at, subject, categories FROM emails WHERE processed = 0"
                        " AND received_at >= date('now','-7 day') ORDER BY received_at DESC").fetchall()
    threads = {}                       # una alerta por conversación (sin RE:/FW:), con el último mensaje
    for r in rows:
        key = re.sub(r"^(?:\s*(?:re|fw|fwd|rv)\s*:\s*|\[external\]\s*)+", "", r["subject"] or "", flags=re.I)
        key = re.sub(r"\W+", " ", key).strip().lower()
        t = threads.setdefault(key, {"subject": r["subject"], "date": r["received_at"], "cats": set(), "n": 0})
        t["cats"].update(c for c in (r["categories"] or "").split(",") if c in ACTION_CATEGORIES)
        t["n"] += 1
    for t in threads.values():
        if t["cats"]:
            n = f" ({t['n']} msgs)" if t["n"] > 1 else ""
            yield _a("MEDIA", "Revisar: " + ", ".join(sorted(t["cats"])), (t["date"] or "")[:10],
                     (t["subject"] or "")[:90] + n)


RULES = [blocked_bookings, missing_docs, cutoff_soon, unpaid_bookings, new_invoices, open_invoices, provisional_invoices,
         invoice_vs_booking_payments, paid_without_invoice, payment_credit, tanks_arriving, tanks_ready_pickup,
         tanks_without_next, unreviewed_emails]


def all_alerts(conn):
    out = []
    for rule in RULES:
        out.extend(rule(conn))
    return out
