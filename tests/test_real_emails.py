"""Casos tomados de emails reales (extractos). Si un formato nuevo falla, agrégalo aquí.

python -m unittest discover tests
"""
import sqlite3
import unittest

from kf import email_ingest, ops, parsers
from kf.db import ROOT

DOCS_CROWLEY = """Buenos Días

Le estaremos adjuntando la documentación de embarque de 10 Iso Tanks de
Gasolina CAT40262737
Cliente: MPM Hola Habana SURL
Vessel/Voyage: 455-4 / CUB6309
B/L: CWPS26265124
Puerto de Carga: Port Everglades, FL
Puerto de Descarga: Mariel, Cuba

Gasol Regular 87 E10 en:

   1. Iso Tank# SEGU8074523
   2. Iso Tank# SEGU8100875
   3. Iso Tank# CXTU1098105
   10. Iso Tank# SLZU2405008

Saludos
Vladimir
Procurement Katapulk"""

DOCS_SEABOARD = """Buenos Días
Le estaremos adjuntando la documentación de embarque de 8 Iso Tanks de Gasolina Booking 9196581A
Cliente: MPM Hola Habana SURL
Vessel/Voyage: SEABOARD RANGER / 557
B/L: SMLU9196581A
Puerto de Carga: Miami, FL"""

ROLLED = """Good day,

Thank you for your email.
Booking has been rolled to

CUB6125 - ETD Oct 02, 2026

Xitlaly Orellana, Senior Specialist

--------------- Original Message ---------------
From: Procurement Katapulk [procurement@katapulk.com]
Sent: 9/25/2026 6:55 PM
Subject: Documents Request: CUB6309 – CAT40263782
The two units originally planned (DCIU0860171 and DCIU0860248) sailed on CUB6308 under CAT40249785002 / B/L CWPS26260913."""

SEABOARD_CONFIRM = """Hola,

Números de reserva indicados a continuación y confirmaciones adjuntas. Por favor, llene el formulario SCC adjunto en su totalidad.

BK#9253089A

Mayrelis Chaviano
On Tue, Sep 29, 2026 02:27 PM, "Noriega, Oskar" <Oskar.Noriega@seaboardmarine.com<mailto:Oskar.Noriega@seaboardmarine.com>> wrote:
Previous: 9212404 A
From: Procurement Katapulk <procurement@katapulk.com>
Sent: Tuesday, September 29, 2026 1:41 PM
Subject: Re: Shipper Owns

Confirmed, EMSERPET will be the consignee for this booking (TUSU132180, TUSU132058).
Previous: 9212404 A
Please let us know if there will be a booking proceed with tanks:
TUSU 132180
TUSU 132058"""

PAYMENT_SENT = """Dear Cubaservice, CC CREDIT HOLD and ARCash Teams,

Please find attached the payment confirmation in the amount of $32,192.70, to be allocated as follows:

  * $24,480.00 – Towards US-Reentry/Return Booking CAT40268984 / B/L CWPN26189608 – 18 ISO Tanks – Gasoline
  * $2,720.00 – Towards US-Reentry/Return Booking CAT40268986 / B/L CWPN26189610 – 2 ISO Tanks – Diesel
  * $4,080.00 – Towards US-Reentry/Return Booking CAT40270958 / B/L CWPN26190387 – 3 ISO Tanks – Diesel
  * $912.70 – Towards brokerage/customs fees related to the returning ISO tanks

Total Payment: $32,192.70"""

PAYMENT_RECEIVED = """Good day

Payment has been received:

Bank Reference F0162710C30401
Customer Reference 269SI2342QYO7268
Value Date 09/28/2026
Transaction Amount 32,192.70
Product Type Funds Transfer

Thank you

________________________________
From: Accounts Payable <accounting@katapulk.com>
Sent: Tuesday, September 29, 2026 09:02
Subject: Katapulk Operations Payment Allocation – Return Bookings
Please find attached the payment confirmation in the amount of $32,192.70"""

CROWLEY_INVOICE_FWD = """---------- Forwarded message ---------
De: Crowley Logistics <no-reply@notifications-crowley.com>
Subject: KATAPULK MARKETPLACE LLC- Crowley - Invoice Number CLI26001351679
for CWPS26265124

Invoice CLI26001351679
has been generated for Bill of Lading CWPS26265124. Please see the attached file."""

PAYMENT_SENT_GMAIL = ("Dear Cubaservice, CC CREDIT HOLD and ARCash Teams,\r\n\r\nPlease find attached the payment "
    "confirmation in the amount of *$32,192.70*,\r\nto be allocated as follows:\r\n\r\n"
    "   - *$24,480.00* – Towards US-Reentry/Return Booking *CAT40268984 / B/L\r\n   CWPN26189608* – *18 ISO Tanks – Gasoline*\r\n"
    "   - *$2,720.00* – Towards US-Reentry/Return Booking *CAT40268986 / B/L\r\n   CWPN26189610* – *2 ISO Tanks – Diesel*\r\n"
    "   - *$4,080.00* – Towards US-Reentry/Return Booking *CAT40270958 / B/L\r\n   CWPN26190387* – *3 ISO Tanks – Diesel*\r\n"
    "   - *$912.70* – Towards *brokerage/customs fees related to the returning\r\n   ISO tanks*\r\n\r\n"
    "*Total Payment: $32,192.70*\r\n\r\nKindly confirm receipt of the funds.")

PAYMENT_SENT_ARIEL = ("Dear Cubaservise; @CCCREDITHOLD; @ARCACH;\r\n\r\nAttached the pre-payment confirmation receipt for a "
    "total of ($65,160.00) for each corresponding booking to be allocate it as follow:\r\n\r\n"
    "$38,295.00\tto\tCAT40262737_10 ISO Tanks Gasoline Fuel (WF Lease Equip)-In rotation\r\n"
    "$4,005.00 \tto \tCAT40263690_1 ISO Tanks Diesel Fuel (WF Lease Equip)-In rotation\r\n"
    "$19,245.00 \tto \tCAT40262783_5 ISO Tanks Diesel Fuel (Shipper Own Equip)-In rotation\r\n"
    "$4,005.00 \tto \tCAT40262784_1 SO Tanks Gasoline Fuel (Crowley Lease Equip)-In rotation\r\n\r\n"
    "@Dalia - We understand the total amount its short in $390.00 to fully cover the $4,005.00 for CAT40263690.")

LEGAL ="The Legal Team, PLLC Total due $5968.23 Dear Hugo, Thank you for providing us with the opportunity to serve you."


def mem_db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    return c


class TestRealParsing(unittest.TestCase):
    def test_docs_crowley(self):
        subj = "Documentación de embarque de 10 Iso Tanks de Gasolina para MPM Hola Habana SURL CWPS26265124"
        d = parsers.extract(subj, DOCS_CROWLEY, "procurement@katapulk.com")
        self.assertIn("shipping_docs", parsers.classify(subj, DOCS_CROWLEY))
        self.assertEqual(d["bookings"], ["CAT40262737"])
        self.assertEqual(d["bls"], ["CWPS26265124"])
        self.assertEqual((d["vessel"], d["voyage"]), ("455-4", "CUB6309"))
        self.assertEqual(d["iso_count"], 10)
        self.assertEqual(d["fuel_type"], "gasoline")
        self.assertEqual(d["client"], "MPM Hola Habana SURL")
        self.assertEqual(d["pod"], "Mariel, Cuba")
        self.assertEqual(d["tanks"], ["SEGU8074523", "SEGU8100875", "CXTU1098105", "SLZU2405008"])

    def test_docs_seaboard(self):
        d = parsers.extract("Documentación de embarque SMLU9196581A", DOCS_SEABOARD, "procurement@katapulk.com")
        self.assertEqual(d["bookings"], ["9196581A"])
        self.assertEqual(d["bls"], ["SMLU9196581A"])
        self.assertEqual((d["vessel"], d["voyage"]), ("SEABOARD RANGER", "557"))
        self.assertNotIn("tanks", d)          # SMLU9196581A no es un tanque

    def test_rolled_ignores_quoted_history(self):
        subj = "RE: Documents Request: CUB6309 – CAT40263782 – no cargo on this voyage, please move to a later sailing"
        d = parsers.extract(subj, ROLLED, "cubaservice@crowley.com")
        self.assertIn("booking_change", parsers.classify(subj, ROLLED))
        self.assertEqual(d["bookings"], ["CAT40263782"])       # no toma CAT40249785002 del historial
        self.assertEqual(d["voyage"], "CUB6125")               # no CUB6309 del asunto
        self.assertEqual(d["etd"], "2026-10-02")
        self.assertEqual(d["vendor"], "Crowley")

    def test_seaboard_booking_confirmation(self):
        d = parsers.extract("RE: Shipper Owns", SEABOARD_CONFIRM, "Mayrelis.Chaviano@jacintoport.com")
        self.assertIn("booking_new", parsers.classify("RE: Shipper Owns", SEABOARD_CONFIRM))
        self.assertEqual(d["bookings"], ["9253089A"])          # "9212404 A" es del historial
        self.assertEqual(d["tanks"], ["TUSU132180", "TUSU132058"])
        self.assertEqual(d["vendor"], "Seaboard")

    def test_payment_sent(self):
        d = parsers.extract("Katapulk Operations Payment Allocation – Return Bookings", PAYMENT_SENT,
                            "accounting@katapulk.com", "CubaService@crowley.com, ARCash@crowley.com")
        self.assertEqual(d["amount"], 32192.70)
        self.assertEqual(d["vendor"], "Crowley")
        self.assertEqual([a["booking_no"] for a in d["allocations"]],
                         ["CAT40268984", "CAT40268986", "CAT40270958", None])
        self.assertAlmostEqual(sum(a["amount"] for a in d["allocations"]), 32192.70, places=2)

    def test_payment_sent_real_formats(self):
        d = parsers.extract("Katapulk Operations Payment Allocation – Return Bookings", PAYMENT_SENT_GMAIL,
                            "accounting@katapulk.com", "CubaService@crowley.com")
        self.assertEqual(d["amount"], 32192.70)
        self.assertEqual([a["booking_no"] for a in d["allocations"]],
                         ["CAT40268984", "CAT40268986", "CAT40270958", None])
        self.assertIn("CWPN26189608", d["allocations"][0]["description"])     # línea partida unida

        d = parsers.extract("Katapulk's Crowley/PEV Shipment Payment Allocation", PAYMENT_SENT_ARIEL,
                            "ariel.machado@katapulk.com", "CubaService@crowley.com")
        self.assertEqual(d["amount"], 65160.00)
        self.assertEqual([(a["amount"], a["booking_no"]) for a in d["allocations"]],
                         [(38295.0, "CAT40262737"), (4005.0, "CAT40263690"), (19245.0, "CAT40262783"),
                          (4005.0, "CAT40262784")])

    def test_own_commercial_invoice_not_vendor_invoice(self):
        c = mem_db()
        email_ingest.process_message(c, "ci", None, "accounting@katapulk.com",
                                     "Commercial Invoices – ISO Tank Returns – CAT40268984",
                                     "Invoice 1278 Crowley return tanks Total: $270,900.00")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 0)

    def test_multi_booking_email_does_not_set_voyage(self):
        c = mem_db()
        for b in ("HOU9224051A", "HOU9224272A"):
            ops.save_booking(c, booking_no=b)
        email_ingest.process_message(c, "mb", None, "Alfred.Gutierrez@seaboardmarine.com",
                                     "RE: Bookings HOU9224051A, HOU9224272A",
                                     "HOU9224051A did not make it onto Seaboard Ranger V.556 and is still empty.")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM bookings WHERE voyage IS NOT NULL").fetchone()[0], 0)

    def test_payment_received(self):
        subj = "Re: Katapulk Operations Payment Allocation – Return Bookings"
        d = parsers.extract(subj, PAYMENT_RECEIVED, "ARCash@crowley.com")
        self.assertIn("payment_confirm", parsers.classify(subj, PAYMENT_RECEIVED))
        self.assertEqual(d["amount"], 32192.70)
        self.assertEqual(d["wire"], "F0162710C30401")

    def test_crowley_invoice(self):
        subj = "Fwd: KATAPULK MARKETPLACE LLC- Crowley - Invoice Number CLI26001351679 for CWPS26265124"
        d = parsers.extract(subj, CROWLEY_INVOICE_FWD, "procurement@katapulk.com")
        self.assertEqual(d["invoice_no"], "CLI26001351679")
        self.assertEqual(d["bls"], ["CWPS26265124"])
        self.assertEqual(d["vendor"], "Crowley")

    def test_other_invoice_formats(self):
        self.assertEqual(parsers.extract("Revised Invoice KML 3576 - NueFuel / Katapulk Fuel to Cuba", "")["invoice_no"], "KML3576")
        self.assertEqual(parsers.extract("Revised Invoice KML 3576 - NueFuel", "")["vendor"], "NueFuel")
        d = parsers.extract("Katapulk Marketplace LLC   Ascent Invoice # 1233410", "", "BMickalenko@wfscorp.com")
        self.assertEqual((d["invoice_no"], d["vendor"]), ("1233410", "World Fuel"))

    def test_booking_formats(self):
        d = parsers.extract("Booking HOU9224051A y 9224272A, sub-booking CAT40249785002", "")
        self.assertEqual(d["bookings"], ["HOU9224051A", "9224272A", "CAT40249785002"])
        d = parsers.extract("RE: BK#9224051A- 12 x 40'DRY Units (to carry 25 IBC tote units each) Total 300 Totes", "")
        self.assertEqual((d["bookings"], d["container_count"], d["tote_count"]), (["9224051A"], 12, 300))

    def test_irrelevant_and_internal(self):
        self.assertFalse(parsers.is_relevant("An Invoice from The Legal Team " + LEGAL))
        self.assertTrue(parsers.should_skip("[URGENTE] Agente ISO: 1 cosa(s) que atender"))


class TestRealProcessing(unittest.TestCase):
    def test_docs_create_booking_and_tanks(self):
        c = mem_db()
        subj = "Documentación de embarque de 10 Iso Tanks de Gasolina para MPM Hola Habana SURL CWPS26265124"
        r = email_ingest.process_message(c, "m1", "2026-09-29T16:27:36", "procurement@katapulk.com", subj, DOCS_CROWLEY)
        b = c.execute("SELECT * FROM bookings WHERE booking_no='CAT40262737'").fetchone()
        self.assertEqual((b["carrier"], b["bl_no"], b["voyage"], b["iso_count"], b["docs_complete"]),
                         ("Crowley", "CWPS26265124", "CUB6309", 10, 1))
        t = c.execute("SELECT * FROM iso_tanks WHERE tank_no='SEGU8074523'").fetchone()
        self.assertEqual((t["outbound_booking"], t["client"], t["fuel_type"]),
                         ("CAT40262737", "MPM Hola Habana SURL", "gasoline"))
        self.assertTrue(r["applied"])

    def test_prefix_resolution_and_roll(self):
        c = mem_db()
        ops.save_booking(c, booking_no="CAT40263782", voyage="CUB6309")
        ops.save_booking(c, booking_no="HOU9253089A")
        subj = "RE: Documents Request: CUB6309 – CAT40263782 – no cargo on this voyage, please move to a later sailing"
        email_ingest.process_message(c, "m2", None, "cubaservice@crowley.com", subj, ROLLED)
        b = c.execute("SELECT voyage, etd FROM bookings WHERE booking_no='CAT40263782'").fetchone()
        self.assertEqual((b["voyage"], b["etd"]), ("CUB6125", "2026-10-02"))
        self.assertEqual(email_ingest.resolve_booking(c, "9253089A"), "HOU9253089A")

    def test_stale_subject_does_not_change_voyage(self):
        """Caso real: asunto de CAT40262737 pero el texto habla de los retornos del Quetzal/CUB6124."""
        c = mem_db()
        ops.save_booking(c, booking_no="CAT40262737", voyage="CUB6309")
        email_ingest.process_message(
            c, "wf", None, "JWheeler@wfscorp.com",
            "Katapulk Corrected VGM – Booking# CAT40262737 (10 ISO Gasoline) – pending VGM",
            "Please send the Arrival Notice, IMO and please let us know when the containers are released "
            "for loading for the 21 containers arriving on Quetzal/CUB6124.")
        self.assertEqual(c.execute("SELECT voyage FROM bookings").fetchone()[0], "CUB6309")

    def test_payment_sent_then_confirmed(self):
        c = mem_db()
        email_ingest.process_message(c, "p1", "2026-09-29T15:02:01", "accounting@katapulk.com",
                                     "Katapulk Operations Payment Allocation – Return Bookings", PAYMENT_SENT,
                                     "CubaService@crowley.com")
        p = c.execute("SELECT * FROM payments").fetchone()
        self.assertEqual((p["vendor"], p["amount"], p["concept"]), ("Crowley", 32192.70, "return"))
        email_ingest.process_message(c, "p2", "2026-09-29T15:38:36", "ARCash@crowley.com",
                                     "Re: Katapulk Operations Payment Allocation – Return Bookings", PAYMENT_RECEIVED)
        p = c.execute("SELECT * FROM payments").fetchone()
        self.assertEqual(p["wire_no"], "F0162710C30401")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)   # no duplica


if __name__ == "__main__":
    unittest.main()
