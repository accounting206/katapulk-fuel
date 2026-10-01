"""Lectores de invoices en PDF, con extractos del texto real de cada proveedor."""
import sqlite3
import unittest

from kf import email_ingest, invoice_pdf
from kf.db import ROOT

ASCENT = """ATTACHMENTS: Ascent_Invoice_1231724.pdf
Quantity Units Description Price Extended
I N V O I C EAscent Aviation Group, Inc.
Invoice #
Ordered by:
Invoice Date:
Delivered:
1231724
Ariel Machado
Sep 23 2026
Sep 23 2026
Terms: C.O.D.
Net Gal6113 Ultra Low Sulfur Clear Diesel 6.76458/gal 41,351.88
Net Gal6113 Federal Excise Tax 0.24300/gal 1,485.46
Net Gal6113 Federal LUST 0.00100/gal 6.11
Net Gal6113 US Federal Superfund Excise Tx 0.00429/gal 26.22
Net Gal6113 FL State Excise Tax 0.22000/gal 1,344.86
Net Gal6113 FL Pollution Tax 0.02071/gal 126.60
Net Gal6113 FL Local Tax 0.16900/gal 1,033.10
TOTAL BALANCE DUE BY SEP 23 2026 $0.00
Less Deposit $45,374.23
DCIU0753342-30208
CAT40263690  Regula V. CUB6039
Seals: Top 571760, Bottom 571761
"""

CCS_STATEMENT = """Carrier Credit Services, Inc.
08/28/26  27CLU247071E DCIU0752449 $1,400.00 $1,400.00CWPN2617620708/20/2607/30/26N
09/07/26  17CLU247855E DCIU0860253 $2,700.00 $2,700.00CWPN2617665909/02/2607/30/26N
Subtotal for Crowley USA: $4,100.00"""

PORT_CONSOLIDATED = """Nicki Choy <Nchoy@PortConsolidated.com>
09/22/26
4412644-1Invoice Number: 09/22/26Due Date:
Amount Due: $38,026.29 625894 / 095
914/01  6,125.0000 DYED ULTRA LOW SULFUR DS/BULK GALLONS $5.82000 $35,647.50
Item Note: BOOKING #: CAT
40262783
CONTAINER #: TCLU9085938
"""

USHINE = """FACTURA
UshineTruckingCorp
Número: FAC0026
Fecha: sept 13, 2026
Booking#9224051A/9224301A/9224272A
12 $500.00 0 % $6,000.00
TOTAL: $6,000.00
PAGADA: $0.00
FACTURA
UshineTruckingCorp
Número: FAC0028
Fecha: sept20,2026
Booking#Pendiente
TOTAL: $8,500.00
PAGADA: $0.00"""

NUEFUEL = """NueFuel TX LLC
PROVISIONAL INVOICE
INVOICE:   KML 3576R
09/17/2026 PROVISIONAL DIESEL 10 PPM EXPORT **142,600 US GALLONS *$5.88 $838,488.00
87 OCTANE GASOLINE EXPORT ** 322,400 US GALLONS *$3.78 $1,218,672.00
SUBTOTAL    $2,057,160.00
DEPOSIT     ($514,290.00)
TOTAL DUE PRIOR TO LOADING     $1,542,870.00"""


class TestReaders(unittest.TestCase):
    def test_ascent_with_deposit(self):
        [d] = invoice_pdf.parse(ASCENT)
        self.assertEqual((d["vendor"], d["invoice_no"], d["invoice_date"]), ("World Fuel", "1231724", "2026-09-23"))
        self.assertEqual((d["tank_no"], d["booking_no"]), ("DCIU0753342", "CAT40263690"))
        self.assertEqual((d["gallons"], d["price_per_gal"], d["fuel_amount"]), (6113, 6.76458, 41351.88))
        self.assertAlmostEqual(d["total"], 45374.23, places=2)      # $0 a pagar + depósito
        self.assertAlmostEqual(d["fuel_amount"] + d["other_amount"], 45374.23, places=2)
        self.assertEqual(d["deposit"], 45374.23)

    def test_ccs_statement(self):
        items = {d["invoice_no"]: d for d in invoice_pdf.parse(CCS_STATEMENT)}
        self.assertEqual(items["CLU247855E"]["total"], 2700.0)
        self.assertEqual(items["CLU247855E"]["tank_no"], "DCIU0860253")
        self.assertEqual(items["CLU247071E"]["bl_no"], "CWPN26176207")
        self.assertEqual(items["CLU247071E"]["concept"], "demurrage")

    def test_port_consolidated(self):
        [d] = invoice_pdf.parse(PORT_CONSOLIDATED)
        self.assertEqual((d["invoice_no"], d["total"], d["booking_no"], d["tank_no"]),
                         ("4412644-1", 38026.29, "CAT40262783", "TCLU9085938"))
        self.assertEqual((d["gallons"], d["price_per_gal"]), (6125.0, 5.82))

    def test_ushine(self):
        items = {d["invoice_no"]: d for d in invoice_pdf.parse(USHINE)}
        self.assertEqual(items["FAC0026"]["total"], 6000.0)
        self.assertEqual(items["FAC0026"]["invoice_date"], "2026-09-13")
        self.assertIn("9224301A", items["FAC0026"]["notes"])
        self.assertEqual(items["FAC0028"]["notes"], "booking pendiente")
        self.assertEqual(items["FAC0028"]["invoice_date"], "2026-09-20")

    def test_nuefuel_provisional(self):
        [d] = invoice_pdf.parse(NUEFUEL)
        self.assertEqual((d["invoice_no"], d["total"], d["status"]), ("KML3576R", 2057160.0, "provisional"))
        self.assertIn("514,290.00", d["notes"])


class TestPdfProcessing(unittest.TestCase):
    def test_deposit_invoice_is_paid(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        email_ingest.process_message(c, "a1", "2026-09-23T10:00:00", "BMickalenko@wfscorp.com",
                                     "Katapulk Marketplace LLC   Ascent Invoice # 1231724", ASCENT)
        email_ingest.process_message(c, "a2", "2026-09-23T11:00:00", "JWheeler@wfscorp.com",   # mismo PDF reenviado
                                     "Katapulk Marketplace: Invoice & C of A", ASCENT)
        inv = c.execute("SELECT * FROM v_invoice_status").fetchone()
        self.assertEqual((inv["pay_status"], inv["balance"]), ("paid", 0))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
