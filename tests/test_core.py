"""python -m unittest discover tests"""
import sqlite3
import unittest

from kf import alerts, ops, parsers
from kf.db import ROOT, history


def mem_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    return conn


class TestParsers(unittest.TestCase):
    def test_return_block(self):
        rows = parsers.parse_return_lines(
            "CAT40268984 / CWPN26189608\n18 ISO Tanks\nGasolina\n"
            "CAT40268986 / CWPN26189610\n2 ISO Tanks\nDiésel\n"
            "CAT40270958 / CWPN26190387\n3 ISO Tanks\nDiésel\n")
        self.assertEqual([r["booking_no"] for r in rows], ["CAT40268984", "CAT40268986", "CAT40270958"])
        self.assertEqual(sum(r["iso_count"] for r in rows), 23)
        self.assertEqual([r["residue_type"] for r in rows], ["gasoline", "diesel", "diesel"])
        self.assertEqual(rows[0]["bl_no"], "CWPN26189608")

    def test_booking_change_email(self):
        subj = "Booking HOU9224272A - Vessel change / revised cutoff"
        body = "Please note vessel: SEABOARD PRIDE  Voyage: 245N\nCY Cutoff: 10/03/2026 14:00\nDiesel IBC totes"
        cats = parsers.classify(subj, body)
        self.assertIn("booking_change", cats)
        d = parsers.extract(subj, body, "ops@seaboardmarine.com")
        self.assertEqual(d["bookings"], ["HOU9224272A"])
        self.assertEqual(d["vendor"], "Seaboard")
        self.assertEqual(d["voyage"], "245N")
        self.assertEqual(parsers.to_iso_date(d["cutoff"]), "2026-10-03 14:00")
        self.assertEqual(d["fuel_type"], "diesel")

    def test_invoice_email(self):
        subj = "Invoice INV-20931 Triton Energy"
        body = "Booking CAT40268984\n2 ISO Tanks gasoline 12,000 gallons\nTotal: $45,210.50"
        d = parsers.extract(subj, body, "billing@tritonenergy.com")
        self.assertIn("invoice", parsers.classify(subj, body))
        self.assertEqual(d["invoice_no"], "INV-20931")
        self.assertEqual(d["amount"], 45210.50)
        self.assertEqual(d["iso_count"], 2)
        self.assertEqual(d["gallons"], 12000)
        self.assertEqual(d["vendor"], "Triton Energy")

    def test_tank_numbers(self):
        d = parsers.extract("Release", "Tanks CRXU 123456 7 and KTPU1000001 released", "")
        self.assertEqual(d["tanks"], ["CRXU1234567", "KTPU1000001"])


class TestMoney(unittest.TestCase):
    def test_credit(self):
        c = mem_db()
        pid = ops.save_payment(c, pay_date="2026-09-15", vendor="Triton Energy", amount=213418.88)
        ops.save_invoice(c, vendor="Triton Energy", invoice_no="A", invoice_date="2026-09-18", total=98500.00)
        ops.save_invoice(c, vendor="Triton Energy", invoice_no="B", invoice_date="2026-09-22", total=87250.40)
        ops.auto_allocate(c)
        p = c.execute("SELECT * FROM v_payment_status WHERE id=?", (pid,)).fetchone()
        self.assertAlmostEqual(p["credit"], 27668.48, places=2)
        self.assertEqual(p["pay_status"], "with_credit")
        self.assertTrue(any(a["kind"] == "Pago con crédito disponible" for a in alerts.all_alerts(c)))
        # llega otra invoice mayor que el crédito -> queda parcial
        ops.save_invoice(c, vendor="Triton Energy", invoice_no="C", invoice_date="2026-09-25", total=30000)
        ops.auto_allocate(c)
        inv = c.execute("SELECT * FROM v_invoice_status WHERE invoice_no='C'").fetchone()
        self.assertEqual(inv["pay_status"], "partial")
        self.assertAlmostEqual(inv["balance"], 2331.52, places=2)


class TestTankLifecycle(unittest.TestCase):
    def test_cycle_and_history(self):
        c = mem_db()
        ops.save_booking(c, booking_no="CAT40000001", direction="outbound", fuel_type="gasoline", etd="2026-09-01")
        ops.assign_tanks(c, "CAT40000001", ["KTPU1000001"])
        ops.set_booking_status(c, "CAT40000001", "shipped"); ops.propagate_booking_status(c, "CAT40000001")
        t = c.execute("SELECT * FROM iso_tanks").fetchone()
        self.assertEqual((t["status"], t["fill_state"]), ("in_transit_cuba", "full"))

        ops.save_booking(c, booking_no="CAT40268984", direction="return", residue_type="gasoline", eta="2026-10-02")
        ops.assign_tanks(c, "CAT40268984", ["KTPU1000001"])
        ops.set_booking_status(c, "CAT40268984", "arrived"); ops.propagate_booking_status(c, "CAT40268984")
        ops.release_return_tanks(c, "CAT40268984")
        t = c.execute("SELECT * FROM iso_tanks").fetchone()
        self.assertEqual((t["status"], t["location"]), ("ready_pickup", "Port Everglades"))
        self.assertTrue(any(a["kind"] == "ISO Tanks sin próximo booking" for a in alerts.all_alerts(c)))

        ops.plan_reuse(c, ["KTPU1000001"], "CAT40999999")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM tank_trips").fetchone()[0], 2)
        fields = [h["field"] for h in history(c, "tank", "KTPU1000001")]
        self.assertIn("status", fields)
        self.assertIn("next_booking", fields)


if __name__ == "__main__":
    unittest.main()
