"""Línea de comandos. Ejemplos:

  python cli.py init
  python cli.py booking CAT40268984 direction=return residue_type=gasoline iso_count=18 bl_no=CWPN26189608 carrier=Crowley
  python cli.py booking CAT40268984 status=arrived          (propaga el estado a sus tanques)
  python cli.py returns-import retornos.txt carrier=Crowley pod="Port Everglades"
  python cli.py assign CAT40268984 CRXU1234567 CRXU7654321
  python cli.py tank CRXU1234567 owner=crowley
  python cli.py release CAT40268984
  python cli.py reuse HOU9230000A CRXU1234567 CRXU7654321
  python cli.py ibc HOU9224051A 300 275 vendor=MidTex fill_state=full
  python cli.py invoice "Triton Energy" INV-1001 45210.50 booking_no=CAT40268984 gallons=60000
  python cli.py pay "Triton Energy" 213418.88 wire_no=FW12345 concept=fuel pay_date=2026-09-20
  python cli.py apply 1 3 45210.50        (aplica $ del pago 1 a la invoice 3)
  python cli.py auto-apply                (aplica créditos automáticamente)
  python cli.py balance
  python cli.py alerts
  python cli.py report --out reportes/
  python cli.py history tank CRXU1234567
  python cli.py email                     (lee Gmail)
  python cli.py email-file mensaje.eml    (procesa un .eml/.txt guardado, para pruebas)
"""
import argparse
import email
import json
import sys
from datetime import date
from pathlib import Path

from kf import alerts, ops, parsers, reports
from kf.db import connect, history, init_db

INT_FIELDS = {"iso_count", "container_count", "docs_complete", "hazmat_approved", "paid", "arrival_notice",
              "released", "brokerage_done", "customs_cleared", "pickup_available", "tote_count", "capacity_gal"}
FLOAT_FIELDS = {"gallons", "price_per_gal", "fuel_amount", "freight_amount", "other_amount", "total", "amount"}


def kv(pairs):
    out = {}
    for p in pairs:
        if "=" not in p:
            sys.exit(f"Formato esperado campo=valor, recibí: {p}")
        k, v = p.split("=", 1)
        if v.lower() in ("si", "sí", "yes", "true"):
            v = 1
        elif v.lower() in ("no", "false"):
            v = 0
        elif k in INT_FIELDS:
            v = int(v)
        elif k in FLOAT_FIELDS:
            v = float(v.replace(",", ""))
        elif k in ("cutoff", "etd", "eta", "pay_date", "invoice_date", "fill_date", "load_date"):
            v = parsers.to_iso_date(v)
        out[k] = v
    return out


def table(rows, cols=None):
    rows = list(rows)
    if not rows:
        print("(sin datos)")
        return
    cols = cols or rows[0].keys()
    data = [[("" if r[c] is None else f"{r[c]:,.2f}" if isinstance(r[c], float) else str(r[c])) for c in cols] for r in rows]
    w = [max(len(c), *(len(d[i]) for d in data)) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    print("  ".join("-" * x for x in w))
    for d in data:
        print("  ".join(d[i].ljust(w[i]) for i in range(len(cols))))


def _pairs(s):
    """'FAC0017:1000,FAC0023:9000' -> [('FAC0017', 1000.0), ('FAC0023', 9000.0)] (montos sin comas)."""
    if not s:
        return []
    out = []
    for item in str(s).split(","):
        k, v = item.split(":")
        out.append((k.strip(), float(v)))
    return out


# Comandos que cambian datos a mano: se guardan en manual_log y `rebuild` los vuelve a aplicar.
# ("apply" no se guarda porque usa ids que cambian al reconstruir; usar `pay ... invoices=` en su lugar)
MANUAL = {"booking", "returns-import", "assign", "tank", "release", "reuse", "ibc", "ibc-status", "invoice", "pay"}


def main(argv=None, replay=False):
    ap = argparse.ArgumentParser(description="Katapulk Fuel Ops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("booking"); p.add_argument("booking_no"); p.add_argument("fields", nargs="*")
    p = sub.add_parser("returns-import"); p.add_argument("file"); p.add_argument("fields", nargs="*")
    p = sub.add_parser("assign"); p.add_argument("booking_no"); p.add_argument("tanks", nargs="+")
    p = sub.add_parser("tank"); p.add_argument("tank_no"); p.add_argument("fields", nargs="*")
    p = sub.add_parser("release"); p.add_argument("booking_no")
    p = sub.add_parser("reuse"); p.add_argument("next_booking"); p.add_argument("tanks", nargs="+")
    p = sub.add_parser("ibc"); p.add_argument("booking_no"); p.add_argument("tote_count", type=int)
    p.add_argument("capacity_gal", type=int); p.add_argument("fields", nargs="*")
    p = sub.add_parser("ibc-status"); p.add_argument("booking_no"); p.add_argument("fields", nargs="+")
    p = sub.add_parser("invoice"); p.add_argument("vendor"); p.add_argument("invoice_no")
    p.add_argument("total", type=float); p.add_argument("fields", nargs="*")
    p = sub.add_parser("pay"); p.add_argument("vendor"); p.add_argument("amount", type=float)
    p.add_argument("fields", nargs="*")
    p = sub.add_parser("apply"); p.add_argument("payment_id", type=int); p.add_argument("invoice_id", type=int)
    p.add_argument("amount", type=float)
    p = sub.add_parser("auto-apply"); p.add_argument("--vendor")
    p.add_argument("--reset", action="store_true", help="recalcula todas las aplicaciones automáticas")
    p = sub.add_parser("estados", help="marca como llegados a Cuba los embarques con más días que el tránsito normal")
    p.add_argument("--dias", type=int, default=ops.TRANSIT_DAYS)
    p = sub.add_parser("pagos", help="agente de pagos urgentes: qué pagar primero y por qué")
    p.add_argument("--out", help="carpeta donde guardar el reporte (además de mostrarlo)")
    p.add_argument("--resumen", action="store_true", help="solo la línea de resumen (para notificaciones)")
    sub.add_parser("balance")
    sub.add_parser("bookings")
    sub.add_parser("tanks")
    sub.add_parser("alerts")
    p = sub.add_parser("report"); p.add_argument("--out")
    p = sub.add_parser("history"); p.add_argument("entity", choices=["booking", "tank", "payment", "invoice", "ibc"])
    p.add_argument("key")
    p = sub.add_parser("email"); p.add_argument("--limit", type=int, default=200)
    p = sub.add_parser("email-file"); p.add_argument("file")
    sub.add_parser("reprocess")
    sub.add_parser("rebuild", help="Reconstruye la base: datos_iniciales.py + emails guardados (sin bajar de Gmail)")
    argv = list(sys.argv[1:] if argv is None else argv)
    a = ap.parse_args(argv)

    conn = connect()
    init_db(conn)                      # idempotente: crea lo que falte
    if a.cmd in MANUAL and not replay:
        conn.execute("INSERT INTO manual_log(argv) VALUES (?)", (json.dumps(argv, ensure_ascii=False),))

    if a.cmd == "init":
        print("Base de datos lista.")
    elif a.cmd == "booking":
        f = kv(a.fields)
        print(ops.save_booking(conn, booking_no=a.booking_no.upper(), **f))
        if "status" in f:
            print(f"{ops.propagate_booking_status(conn, a.booking_no.upper())} tanques actualizados")
    elif a.cmd == "returns-import":
        extra = kv(a.fields)
        for r in parsers.parse_return_lines(Path(a.file).read_text(encoding="utf-8")):
            res = ops.save_booking(conn, "import", direction="return", **{**extra, **r})
            print(f"{r['booking_no']}: {res} ({r['iso_count']} ISO, {r['residue_type']})")
    elif a.cmd == "assign":
        ops.assign_tanks(conn, a.booking_no.upper(), a.tanks)
        print(f"{len(a.tanks)} tanques vinculados a {a.booking_no}")
    elif a.cmd == "tank":
        print(ops.save_tank(conn, tank_no=a.tank_no.upper(), **kv(a.fields)))
    elif a.cmd == "release":
        ops.release_return_tanks(conn, a.booking_no.upper())
        print("Tanques listos para pickup")
    elif a.cmd == "reuse":
        ops.plan_reuse(conn, a.tanks, a.next_booking.upper())
        print(f"{len(a.tanks)} tanques asignados al próximo booking {a.next_booking}")
    elif a.cmd == "ibc":
        ops.add_ibc_lot(conn, a.booking_no.upper(), a.tote_count, a.capacity_gal, **kv(a.fields))
        print(f"Lote: {a.tote_count} x {a.capacity_gal} gal = {a.tote_count * a.capacity_gal:,} gal")
    elif a.cmd == "ibc-status":
        ops.update_ibc_lots(conn, a.booking_no.upper(), **kv(a.fields))
    elif a.cmd == "invoice":
        iid = ops.save_invoice(conn, vendor=a.vendor, invoice_no=a.invoice_no, total=a.total, **kv(a.fields))
        print(f"Invoice id {iid}")
    elif a.cmd == "pay":
        f = kv(a.fields)
        f.setdefault("pay_date", date.today().isoformat())
        invoices = _pairs(f.pop("invoices", None))      # invoices=FAC0017:1000,FAC0023:9000
        splits = _pairs(f.pop("splits", None))          # splits=CAT40262783:3830.80,CAT40262784:766.16
        pid = ops.save_payment(conn, vendor=a.vendor, amount=a.amount, **f)
        if splits:
            ops.save_splits(conn, pid, [{"booking_no": b.upper(), "amount": amt} for b, amt in splits])
        for inv_no, amt in invoices:
            row = conn.execute("SELECT id FROM invoices WHERE vendor = ? AND invoice_no = ?", (a.vendor, inv_no)).fetchone()
            iid = row["id"] if row else ops.save_invoice(
                conn, vendor=a.vendor, invoice_no=inv_no, total=amt, invoice_date=f["pay_date"],
                notes="creada desde el pago (no llegó por email)")
            ops.allocate(conn, pid, iid, amt)
        print(f"Pago id {pid}" + (f", aplicado a {len(invoices)} invoice(s)" if invoices else ""))
    elif a.cmd == "apply":
        ops.allocate(conn, a.payment_id, a.invoice_id, a.amount)
    elif a.cmd == "auto-apply":
        for pid, iid, amt in ops.auto_allocate(conn, a.vendor, reset=a.reset):
            print(f"Pago {pid} -> invoice {iid}: ${amt:,.2f}")
    elif a.cmd == "pagos":
        from kf import pagos
        if a.resumen:
            print(pagos.headline(conn))
        else:
            txt = pagos.text_report(conn)
            print(txt)
            if a.out:
                out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
                (out / f"pagos_urgentes_{date.today():%Y-%m-%d}.txt").write_text(txt, encoding="utf-8")
    elif a.cmd == "estados":
        print(f"{ops.estimate_arrivals(conn, a.dias)} bookings marcados como llegados a Cuba (estimado)")
    elif a.cmd == "balance":
        print("\nPAGOS"); table(conn.execute(
            "SELECT id, pay_date, vendor, wire_no, booking_no, amount, applied, credit, pay_status FROM v_payment_status ORDER BY pay_date"))
        print("\nINVOICES"); table(conn.execute(
            "SELECT id, invoice_date, vendor, invoice_no, booking_no, total, applied, balance, pay_status FROM v_invoice_status ORDER BY invoice_date"))
        print("\nPOR PROVEEDOR (balance + = crédito a favor, - = debemos)"); table(conn.execute("SELECT * FROM v_vendor_balance"))
    elif a.cmd == "bookings":
        table(conn.execute("SELECT booking_no, bl_no, carrier, direction, equipment, fuel_type, residue_type, iso_count,"
                           " container_count, vessel, voyage, etd, eta, cutoff, status, docs_complete docs,"
                           " hazmat_approved imo, paid FROM bookings ORDER BY status, etd"))
    elif a.cmd == "tanks":
        table(conn.execute("SELECT tank_no, owner, fuel_type, fill_state, status, location, outbound_booking,"
                           " return_booking, next_booking FROM iso_tanks ORDER BY status, tank_no"))
    elif a.cmd == "alerts":
        for x in alerts.all_alerts(conn):
            print(f"[{x['priority']}] {x['kind']:<32} {x['ref']:<28} {x['message']}")
    elif a.cmd == "report":
        md = reports.daily_report(conn)
        if a.out:
            out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
            f = out / f"reporte_{date.today():%Y-%m-%d}.md"
            f.write_text(md, encoding="utf-8"); print(f"Guardado en {f}")
        else:
            print(md)
    elif a.cmd == "history":
        key = a.key.upper() if a.entity in ("booking", "tank", "ibc") else a.key
        table(history(conn, a.entity, key))
        if a.entity == "tank":
            print("\nVIAJES"); table(conn.execute("SELECT * FROM tank_trips WHERE tank_no = ? ORDER BY id", (key,)))
    elif a.cmd == "email":
        from kf import email_ingest
        for r in email_ingest.run(conn, a.limit):
            print(f"- {r['subject'][:70]}\n  categorías: {', '.join(r['categories']) or '-'}\n"
                  f"  datos: {r['data']}\n  aplicado: {'; '.join(r['applied']) or 'nada (revisar)'}")
    elif a.cmd == "rebuild":
        import shutil
        import datos_iniciales
        from kf import email_ingest
        from kf.db import DB_PATH
        cache = conn.execute("SELECT * FROM email_cache").fetchall()
        manual = conn.execute("SELECT * FROM manual_log ORDER BY id").fetchall()
        conn.close()
        backup = DB_PATH.with_suffix(f".{date.today():%Y%m%d}.bak")
        shutil.copy(DB_PATH, backup)
        DB_PATH.unlink()
        conn = connect(); init_db(conn)
        conn.executemany("INSERT INTO email_cache VALUES (?,?,?,?,?,?)", [tuple(r) for r in cache])
        conn.executemany("INSERT INTO manual_log VALUES (?,?,?)", [tuple(r) for r in manual])
        datos_iniciales.load(conn)
        res = email_ingest.reprocess(conn)
        conn.commit(); conn.close()
        for r in manual:                       # lo cargado a mano se aplica después de los emails
            main(json.loads(r["argv"]), replay=True)
        conn = connect()
        ops.estimate_arrivals(conn)
        n = len(ops.auto_allocate(conn))
        print(f"Base reconstruida (copia anterior: {backup.name}). {len(res)} emails, "
              f"{len(manual)} datos manuales, {n} aplicaciones de pago.")
    elif a.cmd == "reprocess":
        from kf import email_ingest
        res = email_ingest.reprocess(conn)
        print(f"{len(res)} emails reprocesados; {sum(1 for r in res if r['applied'])} con cambios aplicados")
    elif a.cmd == "email-file":
        from kf import email_ingest
        raw = Path(a.file).read_bytes()
        msg = email.message_from_bytes(raw)
        if msg["Subject"]:
            body, _ = email_ingest._body_and_attachments(msg)
            args = (msg["Message-ID"] or a.file, None, msg["From"] or "", msg["Subject"], body, msg["To"] or "")
        else:
            args = (a.file, None, "", Path(a.file).stem, raw.decode("utf-8", "replace"))
        print(email_ingest.process_message(conn, *args) or "Ya procesado o no relevante")
    conn.commit()


if __name__ == "__main__":
    main()
