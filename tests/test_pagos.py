"""Agente de pagos urgentes."""
import sqlite3
import unittest
from datetime import date

from kf import ops, pagos
from kf.db import ROOT

TODAY = date(2026, 9, 30)


def mem_db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    return c


def cache(c, mid, when, sender, subject, body=""):
    c.execute("INSERT INTO email_cache VALUES (?,?,?,?,?,?)", (mid, when, sender, subject, body, ""))


class TestPagos(unittest.TestCase):
    def test_priorities(self):
        c = mem_db()
        # hold de Crowley sin pago posterior -> BLOQUEO
        cache(c, "h1", "2026-09-25T10:00:00", "German.Nochez@crowley.com", "KATAPULK / 455-4 CUB6306 / CREDIT HOLD")
        ops.save_invoice(c, vendor="Crowley", invoice_no="CLI1", total=5000, invoice_date="2026-09-20")
        # Ushine pide pago -> COBRO
        cache(c, "u1", "2026-09-20T10:00:00", "ushinetruckingcorp@yahoo.com", "Resumen de Viajes y Pagos Pendientes")
        ops.save_invoice(c, vendor="Ushine Trucking", invoice_no="FAC0025", total=8500, invoice_date="2026-09-13")
        # Port Consolidated sin cobro reciente -> SALDO
        ops.save_invoice(c, vendor="Port Consolidated", invoice_no="4361849", total=20000, invoice_date="2026-07-01")
        # booking con cutoff en 2 días sin pago -> EMBARQUE
        ops.save_booking(c, booking_no="CAT40999999", carrier="Crowley", cutoff="2026-10-02", status="active")
        # crédito con Triton
        ops.save_payment(c, pay_date="2026-09-17", vendor="Triton Energy", amount=1000)

        items = pagos.urgent_payments(c, TODAY)
        kinds = [(i["tipo"], i["proveedor"]) for i in items]
        self.assertEqual(kinds[0], ("BLOQUEO", "Crowley"))
        self.assertIn(("EMBARQUE", "Crowley"), kinds)
        self.assertIn(("COBRO", "Ushine Trucking"), kinds)
        self.assertIn(("SALDO", "Port Consolidated"), kinds)
        self.assertEqual(kinds[-1], ("CRÉDITO", "Triton Energy"))
        self.assertLess(kinds.index(("EMBARQUE", "Crowley")), kinds.index(("COBRO", "Ushine Trucking")))

    def test_hold_resolved_by_later_payment(self):
        c = mem_db()
        cache(c, "h1", "2026-09-16T10:00:00", "CubaService@crowley.com", "RE: Payment Allocation", "Booking on CREDIT HOLD")
        ops.save_payment(c, pay_date="2026-09-29", vendor="Crowley", amount=32192.70)
        self.assertFalse([i for i in pagos.urgent_payments(c, TODAY) if i["tipo"] == "BLOQUEO"])

    def test_loose_hold_word_is_not_a_block(self):
        c = mem_db()
        cache(c, "w1", "2026-09-24T10:00:00", "JWheeler@wfscorp.com", "Katapulk Corrected VGM",
              "Please hold the containers until the VGM is corrected")
        self.assertFalse([i for i in pagos.urgent_payments(c, TODAY) if i["tipo"] == "BLOQUEO"])


if __name__ == "__main__":
    unittest.main()
