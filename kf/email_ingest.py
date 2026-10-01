"""Lectura de Gmail por IMAP + clasificación + actualización de la base.

Configuración (variables de entorno o archivo .env en la raíz del proyecto):
    KF_GMAIL_USER=tu_correo@gmail.com
    KF_GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx     (contraseña de aplicación de Google)
    KF_GMAIL_QUERY=newer_than:3d (crowley OR seaboard OR triton OR midtex OR nuefuel OR "iso tank" OR invoice OR booking)

Qué se aplica solo y qué queda para revisión:
  * Automático (bookings ya existentes): vessel, voyage, cutoff, ETD/ETA, B/L,
    arrival notice, release, hazmat aprobado. Todo queda en `events` con source=email:<id>.
  * Automático: invoice nueva cuando se reconocen vendor + número + total.
  * Todo lo demás queda en la tabla `emails` con processed=0 para revisión manual.
"""
import email
import imaplib
import io
import json
import os
from datetime import date
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape
import re

from . import invoice_pdf, ops, parsers
from .db import ROOT, log_event


def _load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _decode(s):
    """Decodifica encabezados y une los asuntos largos que vienen partidos en varias líneas."""
    return re.sub(r"\s*\r?\n\s*", " ", str(make_header(decode_header(s or "")))).strip()


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader           # opcional: pip install pypdf
    except ImportError:
        return ""
    try:
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return ""


def _body_and_attachments(msg) -> tuple[str, list[str]]:
    texts, html, names = [], [], []
    for part in msg.walk():
        ctype = part.get_content_type()
        fname = part.get_filename()
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        if fname:
            names.append(_decode(fname))
            if fname.lower().endswith(".pdf"):
                texts.append(_pdf_text(payload))
        elif ctype == "text/plain":
            texts.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
        elif ctype == "text/html":
            raw = payload.decode(part.get_content_charset() or "utf-8", "replace")
            html.append(unescape(re.sub(r"<[^>]+>", " ", raw)))
    body = "\n".join(texts) if any(t.strip() for t in texts) else "\n".join(html)
    return body, names


def _all_mail_folder(imap):
    """'Todos' incluye enviados (documentación de embarque, pagos). Su nombre cambia con el idioma."""
    _, folders = imap.list()
    for f in folders:
        line = f.decode(errors="replace")
        if "\\All" in line:
            return line.rsplit(' "/" ', 1)[-1].strip().strip('"')
    return "INBOX"


def fetch(limit=200):
    """Genera (message_id, fecha, remitente, asunto, cuerpo) de Gmail."""
    _load_env()
    user = os.environ.get("KF_GMAIL_USER", "")
    pwd = os.environ.get("KF_GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not user or not pwd:
        raise SystemExit("Falta KF_GMAIL_USER o KF_GMAIL_APP_PASSWORD en el archivo .env")
    query = os.environ.get("KF_GMAIL_QUERY", "newer_than:3d")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(user, pwd)
    folder = os.environ.get("KF_GMAIL_FOLDER") or _all_mail_folder(imap)
    imap.select(f'"{folder}"', readonly=True)              # solo lectura: no marca como leído
    quoted = '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'
    _, data = imap.search(None, "X-GM-RAW", quoted)
    ids = data[0].split()[-limit:]
    for num in ids:
        _, msg_data = imap.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        body, attachments = _body_and_attachments(msg)
        if attachments:     # al inicio, para que no se pierda al recortar el historial citado
            body = "ATTACHMENTS: " + ", ".join(attachments) + "\n" + body
        try:
            received = parsedate_to_datetime(msg["Date"]).isoformat()
        except Exception:
            received = None
        yield (msg["Message-ID"] or f"imap-{num.decode()}", received,
               _decode(msg["From"]), _decode(msg["Subject"]), body, _decode(msg["To"]))
    imap.logout()


def resolve_booking(conn, bk):
    """Encuentra el booking aunque venga con o sin prefijo (HOU9224051A = 9224051A)."""
    row = conn.execute("SELECT booking_no FROM bookings WHERE booking_no = ?"
                       " OR booking_no LIKE '%' || ? OR ? LIKE '%' || booking_no"
                       " ORDER BY booking_no = ? DESC LIMIT 1", (bk, bk, bk, bk)).fetchone()
    return row["booking_no"] if row else None


def carrier_of(bk):
    return "Crowley" if bk.startswith("CAT") else "Seaboard"


def _find_payment(conn, vendor, amount, received, days=10):
    return conn.execute(
        "SELECT * FROM payments WHERE vendor = ? AND ABS(amount - ?) < 0.01"
        " AND julianday(COALESCE(?, date('now'))) - julianday(pay_date) BETWEEN -2 AND ?"
        " ORDER BY pay_date DESC LIMIT 1", (vendor, amount, (received or "")[:10] or None, days)).fetchone()


def _save_pdf_invoice(conn, inv, message_id, src):
    """Guarda una invoice leída del PDF, la relaciona con booking/tanque y registra depósitos ya aplicados."""
    inv = dict(inv)
    deposit = inv.pop("deposit", None)
    if inv.get("booking_no"):
        inv["booking_no"] = resolve_booking(conn, inv["booking_no"]) or inv["booking_no"]
    elif inv.get("bl_no"):
        row = conn.execute("SELECT booking_no FROM bookings WHERE bl_no = ?", (inv["bl_no"],)).fetchone()
        inv["booking_no"] = row["booking_no"] if row else None
    iid = ops.save_invoice(conn, src, source_email_id=message_id, **inv)
    # invoice de combustible por tanque: el tanque se cargó con ese proveedor (lo crea si aún no existe;
    # la documentación de embarque suele llegar después y lo vincula al booking)
    t = inv.get("tank_no")
    if t and inv.get("concept") == "fuel":
        ops.save_tank(conn, src, tank_no=t, vendor=inv["vendor"], fuel_type=parsers.detect_fuel(inv.get("product") or ""),
                      load_date=inv.get("invoice_date"))
    # "Less Deposit": la invoice ya viene pagada con un depósito -> se registra ese pago aplicado a ella
    if deposit:
        tag = f"depósito aplicado en invoice {inv['invoice_no']}"
        if not conn.execute("SELECT 1 FROM payments WHERE vendor = ? AND notes = ?", (inv["vendor"], tag)).fetchone():
            pid = ops.save_payment(conn, src, pay_date=inv.get("invoice_date") or date.today().isoformat(),
                                   vendor=inv["vendor"], amount=deposit, booking_no=inv.get("booking_no"),
                                   concept="deposit", notes=tag)
            ops.allocate(conn, pid, iid, deposit, src)


def process_message(conn, message_id, received, sender, subject, body, to="") -> dict | None:
    """Clasifica, guarda y aplica lo seguro. Devuelve el resumen o None si ya existía / no aplica."""
    if conn.execute("SELECT 1 FROM emails WHERE message_id = ?", (message_id,)).fetchone():
        return None
    if parsers.should_skip(subject) or not parsers.is_relevant(f"{sender} {subject} {body}"):
        return None
    conn.execute("INSERT OR IGNORE INTO email_cache VALUES (?,?,?,?,?,?)",
                 (message_id, received, sender, subject, body, to))
    cats = parsers.classify(subject, body)
    data = parsers.extract(subject, body, sender, to)
    src = f"email:{message_id}"
    own = parsers.is_own(sender)
    applied = []
    bookings = data.get("bookings", [])

    # ---- "Documentación de embarque" (enviada por nosotros): fuente confiable -> crea/actualiza
    if "shipping_docs" in cats and own and len(bookings) == 1:
        bk = resolve_booking(conn, bookings[0]) or bookings[0]
        fields = dict(direction="outbound", carrier=carrier_of(bk), fuel_type=data.get("fuel_type"),
                      iso_count=data.get("iso_count"), vessel=data.get("vessel"), voyage=data.get("voyage"),
                      pol=data.get("pol"), pod=data.get("pod"), docs_complete=1)
        if len(data.get("bls", [])) == 1:
            fields["bl_no"] = data["bls"][0]
        # La documentación de embarque (con B/L) se envía al cliente cuando la carga ya salió:
        # el booking pasa a 'shipped' y el IMO/Hazmat tuvo que estar aprobado para embarcar.
        cur = conn.execute("SELECT status FROM bookings WHERE booking_no = ?", (bk,)).fetchone()
        if cur is None or cur["status"] in ("active", "loaded"):
            fields.update(status="shipped", hazmat_approved=1)
        r = ops.save_booking(conn, src, booking_no=bk, **fields)
        applied.append(f"booking {bk} {r} (documentación de embarque)")
        if data.get("tanks"):
            ops.assign_tanks(conn, bk, data["tanks"], src)
            for t in data["tanks"]:
                ops.save_tank(conn, src, tank_no=t, client=data.get("client"))
            applied.append(f"{len(data['tanks'])} tanques vinculados a {bk}")
        ops.propagate_booking_status(conn, bk, src, when=received)
        bookings = []          # ya aplicado

    # ---- cambios en bookings que ya conocemos
    # Fechas/vessel/voyage solo si el email habla de UN booking (si nombra varios, no se sabe de cuál es).
    # A/N, release y hazmat solo cuentan si los manda la naviera/proveedor, no si nosotros los pedimos.
    top = parsers.latest_part(body)
    for raw in bookings:
        bk = resolve_booking(conn, raw)
        if not bk:
            continue
        upd = {}
        # El asunto de un hilo suele quedar viejo: el dato solo vale si el booking está en el texto nuevo
        # (excepción: "rolled to CUBxxxx" de la naviera, que responde bajo el asunto del booking).
        core = re.sub(r"^(?:HOU|MIA)", "", raw)
        in_text = core in parsers.norm_booking(top) or bool(data.get("rolled"))
        if len(bookings) == 1 and in_text:
            upd = {k: data.get(k) for k in ("vessel", "voyage", "cutoff", "etd", "eta")}
            if len(data.get("bls", [])) == 1:
                upd["bl_no"] = data["bls"][0]
        if not own:
            if "arrival_notice" in cats and not re.search(r"(?:send|provide|request)\w*[^.\n]{0,40}arrival notice", top, re.I):
                upd["arrival_notice"] = 1
            if "release" in cats:
                upd["released"] = 1
            if "hazmat" in cats and re.search(r"\bapprov", top, re.I):
                upd["hazmat_approved"] = 1
        if "doc_problem" in cats:
            log_event(conn, "booking", bk, "alert", "doc_problem", None, subject, src)
        r = ops.save_booking(conn, src, booking_no=bk, **upd)
        if r != "unchanged":
            applied.append(f"booking {bk} {r}")

    vendor = data.get("vendor")
    amount = data.get("amount")

    # ---- invoice nueva (si el total viene solo en el PDF, queda para revisión)
    # ---- invoices en PDF con formato conocido (World Fuel, CCS, Port Consolidated, NueFuel, Ushine)
    pdf_invoices = invoice_pdf.parse(body)
    for inv in pdf_invoices:
        _save_pdf_invoice(conn, inv, message_id, src)
    if pdf_invoices:
        by_vendor = {}
        for inv in pdf_invoices:
            by_vendor.setdefault(inv["vendor"], []).append(inv)
        for v, items in by_vendor.items():
            applied.append(f"{len(items)} invoice(s) {v} desde PDF: ${sum(i['total'] for i in items):,.2f}")

    # Los emails propios solo cuentan si reenvían la invoice del proveedor (Fwd:);
    # si no, suelen ser facturas comerciales de Katapulk, no cobros del proveedor.
    forwarded = bool(re.match(r"^\s*(?:fwd?|rv|reenv)\s*:", subject, re.I))
    if ("invoice" in cats and vendor and data.get("invoice_no") and amount and "payment_sent" not in cats
            and not pdf_invoices
            and (not own or forwarded)):
        bk = None
        if len(data.get("bookings", [])) == 1:
            bk = resolve_booking(conn, data["bookings"][0]) or data["bookings"][0]
        elif len(data.get("bls", [])) == 1:     # Crowley factura por B/L
            row = conn.execute("SELECT booking_no FROM bookings WHERE bl_no = ?", (data["bls"][0],)).fetchone()
            bk = row["booking_no"] if row else None
        ops.save_invoice(conn, src, vendor=vendor, invoice_no=data["invoice_no"],
                         invoice_date=(received or "")[:10] or None, booking_no=bk,
                         iso_count=data.get("iso_count"), gallons=data.get("gallons"),
                         product=data.get("fuel_type"), total=amount,
                         source_email_id=message_id, notes="auto desde email - verificar")
        applied.append(f"invoice {vendor} #{data['invoice_no']} ${amount:,.2f}")

    # ---- pago que enviamos nosotros ("payment confirmation in the amount of ... allocated as follows")
    if "payment_sent" in cats and own and vendor and amount:
        if _find_payment(conn, vendor, amount, received):
            applied.append(f"pago {vendor} ${amount:,.2f} ya registrado")
        else:
            allocs = data.get("allocations", [])
            bks = {a["booking_no"] for a in allocs if a["booking_no"]}
            notes = "; ".join(f"${a['amount']:,.2f} {a['description']}" for a in allocs)
            pid = ops.save_payment(conn, src, pay_date=(received or date.today().isoformat())[:10], vendor=vendor,
                                   amount=amount, booking_no=bks.pop() if len(bks) == 1 else None,
                                   concept="return" if re.search(r"re-?entry|return", top, re.I) else None,
                                   notes=notes or None)
            ops.save_splits(conn, pid, allocs, src)
            applied.append(f"pago #{pid} {vendor} ${amount:,.2f} registrado ({len(allocs)} bookings)")

    # ---- confirmación del proveedor de que recibió el pago
    elif "payment_confirm" in cats and not own and vendor and amount:
        p = _find_payment(conn, vendor, amount, received)
        if p:
            if data.get("wire") and not p["wire_no"]:
                conn.execute("UPDATE payments SET wire_no = ? WHERE id = ?", (data["wire"], p["id"]))
            log_event(conn, "payment", p["id"], "confirmed", "bank_ref", None, data.get("wire"), src)
            applied.append(f"pago #{p['id']} confirmado por {vendor}")

    conn.execute(
        "INSERT INTO emails(message_id, received_at, sender, subject, categories, extracted, processed)"
        " VALUES (?,?,?,?,?,?,?)",
        (message_id, received, sender, subject, ",".join(cats), json.dumps(data), 1 if applied else 0))
    conn.commit()
    return {"subject": subject, "categories": cats, "data": data, "applied": applied}


def reprocess(conn):
    """Vuelve a pasar todos los emails guardados por el lector (del más viejo al más nuevo)."""
    rows = conn.execute("SELECT * FROM email_cache ORDER BY received_at").fetchall()
    conn.execute("DELETE FROM emails")
    results = []
    for r in rows:
        res = process_message(conn, r["message_id"], r["received_at"], r["sender"], r["subject"],
                              r["body"], r["recipients"] or "")
        if res:
            results.append(res)
    return results


def run(conn, limit=200):
    """Usa la API de Google si existe credentials.json; si no, IMAP con contraseña de aplicación."""
    if (ROOT / "credentials.json").exists() or os.environ.get("KF_GOOGLE_TOKEN"):
        from .gmail_api import fetch as source
    else:
        source = fetch
    results = []
    for msg in source(limit):
        try:
            r = process_message(conn, *msg)
        except Exception as e:          # un email raro no debe detener la carga
            conn.rollback()
            r = {"subject": msg[3], "categories": [], "data": {}, "applied": [f"ERROR: {e!r}"]}
        if r:
            results.append(r)
    return results
