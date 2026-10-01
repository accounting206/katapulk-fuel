"""Operaciones del negocio: bookings, ISO tanks, IBC totes, invoices y pagos.

Todas pasan por db.upsert / db.log_event, así cada cambio queda en el historial.
"""
from datetime import date, timedelta

from .db import log_event, upsert


# ----------------------------------------------------------------- bookings
def save_booking(conn, source="manual", **fields):
    return upsert(conn, "bookings", "booking_no", fields, "booking", source)


def set_booking_status(conn, booking_no, status, source="manual"):
    return save_booking(conn, source, booking_no=booking_no, status=status)


# ----------------------------------------------------------------- ISO tanks
# Estado del tanque que implica cada estado del booking.
_TANK_STATUS_FROM_BOOKING = {
    ("outbound", "loaded"):    dict(status="loaded", fill_state="full"),
    ("outbound", "shipped"):   dict(status="in_transit_cuba", fill_state="full", location="in transit"),
    ("outbound", "arrived"):   dict(status="in_cuba", location="Cuba"),
    ("return", "shipped"):     dict(status="returning", fill_state="empty", location="in transit"),
    ("return", "returning"):   dict(status="returning", fill_state="empty", location="in transit"),
    ("return", "arrived"):     dict(status="in_us_hold", fill_state="empty"),
}


def save_tank(conn, source="manual", **fields):
    return upsert(conn, "iso_tanks", "tank_no", fields, "tank", source)


def assign_tanks(conn, booking_no, tank_nos, source="manual"):
    """Vincula tanques a un booking (salida o retorno) y abre un 'trip' por tanque."""
    b = conn.execute("SELECT * FROM bookings WHERE booking_no = ?", (booking_no,)).fetchone()
    if b is None:
        raise ValueError(f"Booking {booking_no} no existe")
    leg = b["direction"]
    for t in tank_nos:
        t = t.strip().upper()
        link = {"outbound_booking": booking_no} if leg == "outbound" else {"return_booking": booking_no}
        # si este booking era el 'próximo' del tanque, ya se consumió
        tank = conn.execute("SELECT next_booking FROM iso_tanks WHERE tank_no=?", (t,)).fetchone()
        if tank and tank["next_booking"] == booking_no:
            conn.execute("UPDATE iso_tanks SET next_booking = NULL WHERE tank_no = ?", (t,))
        save_tank(conn, source, tank_no=t, fuel_type=b["fuel_type"] or b["residue_type"], **link)
        cur = conn.execute(
            "INSERT OR IGNORE INTO tank_trips(tank_no, booking_no, leg, fuel_type, start_date)"
            " VALUES (?,?,?,?,?)",
            (t, booking_no, leg, b["fuel_type"] or b["residue_type"], b["etd"]))
        if cur.rowcount:
            log_event(conn, "tank", t, "linked", "booking", None, f"{leg}:{booking_no}", source)


def propagate_booking_status(conn, booking_no, source="manual", when=None):
    """Cuando cambia el status de un booking, actualiza sus tanques.
    `when` = fecha del hecho (p. ej. fecha del email); si falta, se usa hoy."""
    b = conn.execute("SELECT * FROM bookings WHERE booking_no = ?", (booking_no,)).fetchone()
    changes = _TANK_STATUS_FROM_BOOKING.get((b["direction"], b["status"]))
    if not changes:
        return 0
    today = (when or date.today().isoformat())[:10]
    col = "outbound_booking" if b["direction"] == "outbound" else "return_booking"
    tanks = [r["tank_no"] for r in conn.execute(f"SELECT tank_no FROM iso_tanks WHERE {col} = ?", (booking_no,))]
    for t in tanks:
        extra = {}
        if b["direction"] == "outbound" and b["status"] == "shipped":
            extra["departure_date"] = b["etd"] or today
        if b["direction"] == "outbound" and b["status"] == "arrived":
            extra["cuba_arrival_date"] = b["eta"] or today
        if b["direction"] == "return" and b["status"] == "arrived":
            extra["us_return_date"] = b["eta"] or today
            extra["location"] = b["pod"] or "Port Everglades"
        save_tank(conn, source, tank_no=t, **changes, **extra)
    return len(tanks)


TRANSIT_DAYS = 4      # Port Everglades/Miami/Houston -> Mariel: la carga llega en ~2-4 días


def estimate_arrivals(conn, days=TRANSIT_DAYS, today=None):
    """Bookings de salida embarcados hace más de `days` días pasan a 'arrived' (tanques 'en Cuba').
    Es una estimación por tiempo de tránsito: queda en el historial con source='estimado'."""
    today = today or date.today()
    n = 0
    for b in conn.execute("SELECT * FROM bookings WHERE direction = 'outbound' AND status = 'shipped'").fetchall():
        dep = conn.execute("SELECT MAX(departure_date) FROM iso_tanks WHERE outbound_booking = ?",
                           (b["booking_no"],)).fetchone()[0] or b["etd"]
        if not dep:
            continue
        arrival = date.fromisoformat(dep[:10]) + timedelta(days=days)
        if arrival <= today:
            save_booking(conn, "estimado", booking_no=b["booking_no"], status="arrived",
                         eta=b["eta"] or arrival.isoformat())
            propagate_booking_status(conn, b["booking_no"], "estimado", when=arrival.isoformat())
            n += 1
    return n


def release_return_tanks(conn, booking_no, source="manual"):
    """Retorno liberado (release + customs): tanques listos para pickup y reuso."""
    save_booking(conn, source, booking_no=booking_no, released=1, pickup_available=1)
    for r in conn.execute("SELECT tank_no FROM iso_tanks WHERE return_booking = ?", (booking_no,)).fetchall():
        save_tank(conn, source, tank_no=r["tank_no"], status="ready_pickup", fill_state="empty")


def plan_reuse(conn, tank_nos, next_booking, source="manual"):
    for t in tank_nos:
        save_tank(conn, source, tank_no=t.strip().upper(), next_booking=next_booking)


# ----------------------------------------------------------------- IBC totes
def add_ibc_lot(conn, booking_no, tote_count, capacity_gal, source="manual", **fields):
    cur = conn.execute(
        "INSERT INTO ibc_lots(booking_no, tote_count, capacity_gal, vendor, fill_date, fill_state, location)"
        " VALUES (?,?,?,?,?,?,?)",
        (booking_no, tote_count, capacity_gal, fields.get("vendor"), fields.get("fill_date"),
         fields.get("fill_state", "empty"), fields.get("location", "port")))
    log_event(conn, "ibc", booking_no, "created", "lot",
              None, f"{tote_count} x {capacity_gal} gal", source)
    return cur.lastrowid


def update_ibc_lots(conn, booking_no, source="manual", **fields):
    for lot in conn.execute("SELECT * FROM ibc_lots WHERE booking_no = ?", (booking_no,)).fetchall():
        for k, v in fields.items():
            if str(lot[k]) != str(v):
                conn.execute(f"UPDATE ibc_lots SET {k} = ?, updated_at = datetime('now') WHERE id = ?", (v, lot["id"]))
                log_event(conn, "ibc", booking_no, "status_change", k, lot[k], v, source)


# ----------------------------------------------------------------- dinero
def save_invoice(conn, source="manual", **fields):
    """Crea/actualiza invoice (clave: vendor + invoice_no). Devuelve id.
    Al actualizar, los campos vacíos no borran lo que ya había; el total cambiado queda en el historial."""
    fields = {k: v for k, v in fields.items() if v is not None}
    row = conn.execute("SELECT * FROM invoices WHERE vendor = ? AND invoice_no = ?",
                       (fields["vendor"], fields["invoice_no"])).fetchone()
    if row:
        changed = {k: v for k, v in fields.items() if str(row[k]) != str(v)}
        if changed:
            sets = ", ".join(f"{k} = ?" for k in changed)
            conn.execute(f"UPDATE invoices SET {sets} WHERE id = ?", (*changed.values(), row["id"]))
            key = f"{fields['vendor']}#{fields['invoice_no']}"
            for k, v in changed.items():
                log_event(conn, "invoice", key, "updated", k, row[k], v, source)
        return row["id"]
    cols = ", ".join(fields)
    cur = conn.execute(f"INSERT INTO invoices ({cols}) VALUES ({', '.join('?' for _ in fields)})",
                       tuple(fields.values()))
    log_event(conn, "invoice", f"{fields['vendor']}#{fields['invoice_no']}", "created",
              "total", None, fields["total"], source)
    return cur.lastrowid


def save_payment(conn, source="manual", **fields):
    cols = ", ".join(fields)
    cur = conn.execute(f"INSERT INTO payments ({cols}) VALUES ({', '.join('?' for _ in fields)})",
                       tuple(fields.values()))
    log_event(conn, "payment", cur.lastrowid, "created", "amount", None, fields["amount"], source)
    return cur.lastrowid


def allocate(conn, payment_id, invoice_id, amount, source="manual"):
    conn.execute("INSERT INTO payment_allocations(payment_id, invoice_id, amount) VALUES (?,?,?)",
                 (payment_id, invoice_id, round(amount, 2)))
    log_event(conn, "payment", payment_id, "applied", "invoice", None, f"{invoice_id}:{amount:.2f}", source)


def save_splits(conn, payment_id, splits, source="manual"):
    """Guarda el reparto del pago por booking y marca esos bookings como pagados."""
    for s in splits:
        conn.execute("INSERT INTO payment_splits(payment_id, booking_no, amount, description) VALUES (?,?,?,?)",
                     (payment_id, s.get("booking_no"), s["amount"], s.get("description")))
        if s.get("booking_no") and conn.execute("SELECT 1 FROM bookings WHERE booking_no = ?",
                                                (s["booking_no"],)).fetchone():
            save_booking(conn, source, booking_no=s["booking_no"], paid=1)


def _apply(conn, payment_id, invoice_id, amt, done, source):
    amt = round(amt, 2)
    conn.execute(
        "INSERT INTO payment_allocations(payment_id, invoice_id, amount, auto) VALUES (?,?,?,1)"
        " ON CONFLICT(payment_id, invoice_id) DO UPDATE SET amount = amount + excluded.amount",
        (payment_id, invoice_id, amt))
    log_event(conn, "payment", payment_id, "applied", "invoice", None, f"{invoice_id}:{amt:.2f}", source)
    done.append((payment_id, invoice_id, amt))


def _credit(conn, pid):
    return conn.execute("SELECT credit FROM v_payment_status WHERE id = ?", (pid,)).fetchone()["credit"]


def _open_invoices(conn, vendor, bookings=None, no_booking=False):
    q, args = ("SELECT id, balance FROM v_invoice_status WHERE vendor = ? AND balance > 0.009"
               " AND status = 'final'", [vendor])
    if bookings:
        q += f" AND booking_no IN ({','.join('?' for _ in bookings)})"; args += list(bookings)
    elif no_booking:
        q += " AND booking_no IS NULL"
    return conn.execute(q + " ORDER BY invoice_date, id", args).fetchall()


def _fill(conn, pid, invoices, left, done, source):
    for inv in invoices:
        left = min(left, _credit(conn, pid))
        if left <= 0.009:
            break
        amt = min(left, inv["balance"])
        _apply(conn, pid, inv["id"], amt, done, source)
        left -= amt


def payment_bookings(p):
    """Bookings que nombra un pago: 'CAT40262783 + CAT40262784' -> ['CAT40262783', 'CAT40262784']."""
    from .parsers import PATTERNS, norm_booking
    return [norm_booking(b) for b in PATTERNS["booking"].findall(p["booking_no"] or "")]


def auto_allocate(conn, vendor=None, source="auto", reset=False):
    """Aplica pagos a invoices abiertas del mismo vendor, respetando a qué booking iba cada dinero.

    Fase 1 (dinero con destino): reparto del pago por booking (payment_splits) y pagos que nombran
      uno o varios bookings -> solo a invoices de esos bookings. Va primero para que un pago general
      no se "coma" invoices que ya tenían su propio pago.
    Fase 2 (dinero general): la parte de los repartos sin booking va a invoices sin booking; los pagos
      sin ninguna referencia van por antigüedad a cualquier invoice abierta del vendor.
    reset=True borra las aplicaciones automáticas anteriores y recalcula (las manuales se respetan).
    Devuelve lista de (payment_id, invoice_id, monto).
    """
    if reset:
        conn.execute("DELETE FROM payment_allocations WHERE auto = 1" +
                     (" AND payment_id IN (SELECT id FROM payments WHERE vendor = ?)" if vendor else ""),
                     (vendor,) if vendor else ())
    done = []
    q = "SELECT * FROM payments" + (" WHERE vendor = ?" if vendor else "") + " ORDER BY pay_date, id"
    pays = conn.execute(q, (vendor,) if vendor else ()).fetchall()
    splits = {p["id"]: conn.execute("SELECT * FROM payment_splits WHERE payment_id = ?", (p["id"],)).fetchall()
              for p in pays}

    for p in pays:                                                   # fase 1
        for s in splits[p["id"]]:
            if not s["booking_no"]:
                continue
            used = conn.execute(
                "SELECT COALESCE(SUM(a.amount),0) FROM payment_allocations a JOIN invoices i ON i.id = a.invoice_id"
                " WHERE a.payment_id = ? AND i.booking_no = ?", (p["id"], s["booking_no"])).fetchone()[0]
            _fill(conn, p["id"], _open_invoices(conn, p["vendor"], [s["booking_no"]]), s["amount"] - used, done, source)
        if not splits[p["id"]] and payment_bookings(p):
            _fill(conn, p["id"], _open_invoices(conn, p["vendor"], payment_bookings(p)), p["amount"], done, source)

    for p in pays:                                                   # fase 2
        if splits[p["id"]]:
            general = sum(s["amount"] for s in splits[p["id"]] if not s["booking_no"])
            used = conn.execute(
                "SELECT COALESCE(SUM(a.amount),0) FROM payment_allocations a JOIN invoices i ON i.id = a.invoice_id"
                " WHERE a.payment_id = ? AND i.booking_no IS NULL", (p["id"],)).fetchone()[0]
            _fill(conn, p["id"], _open_invoices(conn, p["vendor"], no_booking=True), general - used, done, source)
        elif not payment_bookings(p):
            _fill(conn, p["id"], _open_invoices(conn, p["vendor"]), p["amount"], done, source)
    return done
