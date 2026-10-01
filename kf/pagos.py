"""Agente de pagos urgentes: qué pagar primero y por qué.

Casi todos los proveedores cobran al momento (C.O.D., "due upon receipt", flete prepagado), así que la
fecha de vencimiento no ordena nada. El agente prioriza por impacto:

  1. BLOQUEO     un proveedor puso CREDIT HOLD / HOLD y no le hemos pagado desde entonces
  2. EMBARQUE    un booking por salir (cutoff cercano) sin pago al naviero / al proveedor de combustible
  3. DEPÓSITO    invoice provisional que exige depósito antes de cargar
  4. COBRO       proveedor con saldo abierto que nos pidió el pago (email de cobro, estado de cuenta)
  5. SALDO       resto de saldos abiertos, lo más viejo primero
  Además lista los CRÉDITOS a favor, para no pagar de más.

Cada regla es una función pequeña; para agregar una, escribirla y sumarla a RULES.
"""
import re
from datetime import date, datetime, timedelta

from . import parsers

# "hold" suelto aparece en muchos textos; solo cuenta "credit hold" / "on hold" o HOLD en el asunto.
HOLD_RE = re.compile(r"\bcredit hold\b|\bon (?:credit )?hold\b|^[^\n]*\bhold\b", re.I)
REQUEST_RE = re.compile(r"balance due|past due|overdue|please (?:remit|pay)|payment (?:request|due|required)|"
                        r"pendiente[s]? de pago|pagos pendientes|statement|outstanding|reminder|recordatorio", re.I)
CUTOFF_DAYS = 5
REQUEST_DAYS = 14


def _d(s):
    try:
        return datetime.fromisoformat((s or "")[:10]).date()
    except ValueError:
        return None


def _item(priority, kind, vendor, amount, why, action, ref=""):
    return {"prioridad": priority, "tipo": kind, "proveedor": vendor, "monto": round(amount or 0, 2),
            "motivo": why, "accion": action, "ref": ref}


def _last_payment(conn, vendor):
    return _d(conn.execute("SELECT MAX(pay_date) FROM payments WHERE vendor = ?", (vendor,)).fetchone()[0])


def _open_by_vendor(conn):
    q = ("SELECT vendor, COUNT(*) n, ROUND(SUM(balance),2) saldo, MIN(invoice_date) desde, "
         "GROUP_CONCAT(invoice_no, ', ') invs FROM v_invoice_status "
         "WHERE status = 'final' AND balance > 0.009 GROUP BY vendor")
    return {r["vendor"]: r for r in conn.execute(q)}


def _vendor_emails(conn, pattern, days, today):
    """(vendor, fecha, asunto) de emails de proveedores (no nuestros) que cumplen el patrón."""
    since = (today - timedelta(days=days)).isoformat()
    out = []
    for r in conn.execute("SELECT * FROM email_cache WHERE received_at >= ? ORDER BY received_at DESC", (since,)):
        if parsers.is_own(r["sender"]):
            continue
        top = parsers.latest_part((r["body"] or "").replace("\r\n", "\n"))
        if pattern.search(f"{r['subject']}\n{top}"):
            vendor = parsers.detect_vendor(r["sender"], r["subject"], top)
            if vendor:
                out.append((vendor, _d(r["received_at"]), r["subject"]))
    return out


# ------------------------------------------------------------------ reglas
def holds(conn, today):
    seen = set()
    for vendor, when, subject in _vendor_emails(conn, HOLD_RE, 30, today):
        if vendor in seen:
            continue
        seen.add(vendor)
        paid = _last_payment(conn, vendor)
        if paid and when and paid >= when:
            continue                          # ya se pagó después del hold
        saldo = _open_by_vendor(conn).get(vendor)
        yield _item(1, "BLOQUEO", vendor, saldo["saldo"] if saldo else 0,
                    f"{when}: {subject[:80]}",
                    "Pagar / confirmar pago para liberar la carga", when.isoformat() if when else "")


def shipments(conn, today):
    limit = (today + timedelta(days=CUTOFF_DAYS)).isoformat()
    q = ("SELECT * FROM bookings b WHERE direction = 'outbound' AND status IN ('active','loaded') "
         "AND cutoff IS NOT NULL AND substr(cutoff,1,10) <= ? AND NOT EXISTS "
         "(SELECT 1 FROM v_booking_money m WHERE m.booking_no = b.booking_no AND m.paid > 0) AND paid = 0")
    for b in conn.execute(q, (limit,)):
        days = (_d(b["cutoff"]) - today).days if _d(b["cutoff"]) else None
        when = "VENCIDO" if days is not None and days < 0 else f"en {days} día(s)"
        yield _item(2, "EMBARQUE", b["carrier"] or "¿naviera?", 0,
                    f"Booking {b['booking_no']} cutoff {b['cutoff']} ({when}) sin pago registrado",
                    "Pagar flete (prepago) y combustible antes del cutoff", b["booking_no"])


def deposits(conn, today):
    for i in conn.execute("SELECT * FROM invoices WHERE status = 'provisional' AND notes LIKE '%depósito requerido%'"):
        m = re.search(r"depósito requerido \$([\d,]+\.\d\d)", i["notes"])
        amount = float(m.group(1).replace(",", "")) if m else 0
        paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE vendor = ? AND pay_date >= ?",
                            (i["vendor"], i["invoice_date"] or "2000-01-01")).fetchone()[0]
        if paid >= amount > 0:
            continue
        yield _item(3, "DEPÓSITO", i["vendor"], amount - paid,
                    f"Invoice provisional #{i['invoice_no']} (${i['total']:,.0f}) pide depósito antes de cargar",
                    "Pagar depósito si se va a cargar este pedido", i["invoice_no"])


def requested(conn, today):
    asked = {}
    for vendor, when, subject in _vendor_emails(conn, REQUEST_RE, REQUEST_DAYS, today):
        asked.setdefault(vendor, (when, subject))
    for vendor, s in _open_by_vendor(conn).items():
        if vendor not in asked:
            continue
        when, subject = asked[vendor]
        age = (today - _d(s["desde"])).days if _d(s["desde"]) else 0
        yield _item(4, "COBRO", vendor, s["saldo"],
                    f"{s['n']} invoice(s) abiertas, la más vieja de hace {age} días. Último cobro {when}: {subject[:60]}",
                    "Pagar o acordar fecha con el proveedor", (s["invs"] or "")[:120])


def other_balances(conn, today):
    asked = {v for v, _, _ in _vendor_emails(conn, REQUEST_RE, REQUEST_DAYS, today)}
    for vendor, s in sorted(_open_by_vendor(conn).items(), key=lambda kv: kv[1]["desde"] or ""):
        if vendor in asked:
            continue
        age = (today - _d(s["desde"])).days if _d(s["desde"]) else 0
        yield _item(5, "SALDO", vendor, s["saldo"],
                    f"{s['n']} invoice(s) abiertas, la más vieja de hace {age} días (sin cobro reciente)",
                    "Confirmar si ya se pagó (falta registrar el pago) o programar pago", (s["invs"] or "")[:120])


def credits(conn, today):
    q = ("SELECT vendor, ROUND(SUM(credit),2) credito, COUNT(*) n FROM v_payment_status "
         "WHERE credit > 0.009 GROUP BY vendor ORDER BY credito DESC")
    for r in conn.execute(q):
        yield _item(9, "CRÉDITO", r["vendor"], r["credito"],
                    f"{r['n']} pago(s) con dinero sin aplicar a invoices",
                    "No pagar de más: usar este crédito o pedir que lo apliquen / devuelvan")


RULES = [holds, shipments, deposits, requested, other_balances, credits]


def urgent_payments(conn, today=None):
    today = today or date.today()
    items = [it for rule in RULES for it in rule(conn, today)]
    return sorted(items, key=lambda x: (x["prioridad"], -x["monto"]))


def text_report(conn, today=None):
    today = today or date.today()
    items = urgent_payments(conn, today)
    pay = [i for i in items if i["tipo"] != "CRÉDITO"]
    cred = [i for i in items if i["tipo"] == "CRÉDITO"]
    lines = [f"PAGOS URGENTES — {today:%d/%m/%Y}", ""]
    if not pay:
        lines.append("Nada urgente por pagar.")
    for n, i in enumerate(pay, 1):
        lines.append(f"{n}. [{i['tipo']}] {i['proveedor']}" + (f" — ${i['monto']:,.2f}" if i["monto"] else ""))
        lines.append(f"   Por qué: {i['motivo']}")
        lines.append(f"   Qué hacer: {i['accion']}")
        if i["ref"]:
            lines.append(f"   Ref: {i['ref']}")
    total = sum(i["monto"] for i in pay)
    lines += ["", f"Total en la lista: ${total:,.2f}"]
    if cred:
        lines += ["", "CRÉDITOS A FAVOR (no pagar de más):"]
        lines += [f"   {i['proveedor']}: ${i['monto']:,.2f} — {i['motivo']}" for i in cred]
    return "\n".join(lines)


def headline(conn, today=None):
    """Resumen de 1-2 líneas para la notificación de Windows."""
    items = [i for i in urgent_payments(conn, today) if i["tipo"] != "CRÉDITO"]
    if not items:
        return "Nada urgente por pagar hoy."
    top = items[0]
    blocks = sum(1 for i in items if i["tipo"] in ("BLOQUEO", "EMBARQUE", "DEPÓSITO"))
    first = f"{top['tipo']}: {top['proveedor']}" + (f" ${top['monto']:,.0f}" if top["monto"] else "")
    return f"{len(items)} pagos en la lista ({blocks} bloquean operación). Primero → {first}"
