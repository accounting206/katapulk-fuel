"""Lectores de invoices en PDF, uno por formato de proveedor.

Cada lector recibe el texto completo del email (cuerpo + texto de los PDFs adjuntos) y devuelve
una lista de invoices (un PDF puede traer varias, p. ej. un estado de cuenta). Campos posibles:
vendor, invoice_no, invoice_date, booking_no, bl_no, tank_no, product, gallons, price_per_gal,
fuel_amount, other_amount, total, concept, status ('final' | 'provisional'), notes.

Para un proveedor nuevo: escribir una función `_lector(text)` y agregarla a READERS.
"""
import re

from .parsers import detect_fuel

MONEY = r"(\d[\d,]*\.\d{2})"
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _m(s):
    return float(s.replace(",", ""))


def _date_mdy(s):
    """'09/07/26', '9 / 7 / 26' -> '2026-09-07'."""
    m = re.match(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})", s or "")
    if not m:
        return None
    mo, d, y = m.groups()
    return f"{y if len(y) == 4 else '20' + y}-{int(mo):02d}-{int(d):02d}"


def _date_text(s):
    """'Sep 28 2026' -> '2026-09-28'."""
    m = re.match(r"([A-Za-z]{3})[a-z]* (\d{1,2}),? (\d{4})", s or "")
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"


# ------------------------------------------------------------------ Ascent / World Fuel
def _ascent(text):
    """Una invoice por ISO Tank. Varias pueden venir en el mismo email."""
    out = []
    for blk in re.split(r"(?=Quantity Units Description Price Extended)", text):
        if "Ascent Aviation" not in blk or "TOTAL BALANCE DUE" not in blk:
            continue
        inv = re.search(r"Delivered:\s*\n\s*(\d{6,8})\s*\n", blk)
        total = re.search(r"TOTAL BALANCE DUE[^$\n]*\$\s*" + MONEY, blk)
        if not inv or not total:
            continue
        lines = re.findall(r"Net Gal\s*([\d,]+)\s+(.+?)\s+([\d.]+)/gal\s+" + MONEY, blk)
        d = {"vendor": "World Fuel", "invoice_no": inv.group(1), "total": _m(total.group(1)),
             "concept": "fuel", "status": "final"}
        dt = re.search(r"Delivered:\s*\n\s*\d{6,8}\s*\n[^\n]*\n\s*([A-Z][a-z]{2} \d{1,2} \d{4})", blk)
        if dt:
            d["invoice_date"] = _date_text(dt.group(1))
        if lines:
            gal, prod, price, amt = lines[0]
            d.update(gallons=_m(gal), product=prod.strip(), price_per_gal=float(price), fuel_amount=_m(amt),
                     other_amount=round(sum(_m(x[3]) for x in lines[1:]), 2))
        # "TOTAL BALANCE DUE ... $0.00 / Less Deposit $45,374.23": ya pagada con depósito
        dep = re.search(r"Less Deposit\s*\$\s*" + MONEY, blk)
        if dep:
            d["deposit"] = _m(dep.group(1))
            d["total"] = round(d["total"] + d["deposit"], 2)
        tank = re.search(r"\b([A-Z]{3}U\d{7})-\d+", blk)
        if tank:
            d["tank_no"] = tank.group(1)
        bk = re.search(r"\b(CAT\d{8}(?:\d{3})?|9\d{6}[A-Z])\s+([^\n]+?)\s+(CUB\d{4}|\d{3,4})\s*\n", blk)
        if bk:
            d["booking_no"] = bk.group(1)
            d["notes"] = f"vessel {bk.group(2)} / {bk.group(3)}"
        out.append(d)
    return out


# ------------------------------------------------------------------ CCS (demoras de Crowley)
_CCS_LINE = re.compile(
    r"(\d\d/\d\d/\d\d)\s+\d+\s*(CLU\d+E)\s+([A-Z]{4}\d{7})\s+\$" + MONEY + r"\s+\$" + MONEY + r"(CW[A-Z]{2}\d{8})?")


def _ccs(text):
    """Carrier Credit Services cobra el demurrage/storage de Crowley: estado de cuenta e invoices sueltas."""
    if "Carrier Credit Services" not in text:
        return []
    found = {}
    for date, inv, tank, amount, balance, bl in _CCS_LINE.findall(text):      # estado de cuenta
        found[inv] = {"vendor": "CCS (Crowley demurrage)", "invoice_no": inv, "invoice_date": _date_mdy(date),
                      "tank_no": tank, "bl_no": bl or None, "total": _m(amount), "concept": "demurrage",
                      "status": "final", "notes": f"saldo en estado de cuenta ${balance}"}
    for m in re.finditer(r"Total Amount Due\s*\$\s*" + MONEY, text):          # invoices sueltas
        before, after = text[max(0, m.start() - 2500):m.start()], text[m.end():m.end() + 300]
        invs = re.findall(r"(CLU\d+E)", before)
        if not invs:
            continue
        inv = invs[-1]
        d = found.setdefault(inv, {"vendor": "CCS (Crowley demurrage)", "invoice_no": inv,
                                   "total": _m(m.group(1)), "concept": "demurrage", "status": "final"})
        bk = re.search(r"(CAT\d{8})\s*\n\s*Booking No", after)
        if bk:
            d["booking_no"] = bk.group(1)
        if not d.get("bl_no"):
            bl = re.findall(r"(CW[A-Z]{2}\d{8})", before)
            d["bl_no"] = bl[-1] if bl else None
        if not d.get("tank_no"):
            t = re.findall(r"\b([A-Z]{3}U\d{7})\b", before)
            d["tank_no"] = t[-1] if t else None
        if not d.get("invoice_date"):
            dt = re.findall(r"Invoice Date\s*:\s*(\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4})", before)
            d["invoice_date"] = _date_mdy(dt[-1]) if dt else None
    return list(found.values())


# ------------------------------------------------------------------ Port Consolidated
_PC_DIGITAL = re.compile(r"(\d{7}(?:-\d)?)\s*Invoice Number:\s*(\d\d/\d\d/\d\d)\s*Due Date:\s*\n\s*Amount Due:\s*\$\s*" + MONEY)


def _port_consolidated(text):
    """PDF digital (una invoice por ISO Tank: 4412644-1, -2...). Si solo hay escaneo, usa el lector OCR."""
    if "portconsolidated" not in text.lower():
        return []
    out = {}
    for m in _PC_DIGITAL.finditer(text):
        blk = text[m.end():m.end() + 2500]
        d = {"vendor": "Port Consolidated", "invoice_no": m.group(1), "invoice_date": _date_mdy(m.group(2)),
             "total": _m(m.group(3)), "concept": "fuel", "status": "final"}
        line = re.search(r"([\d,]+)\.\d+\s+(.+?)\s+GALLONS\s+\$\s*([\d.]+)\s+\$\s*" + MONEY, blk)
        if line:
            d.update(gallons=_m(line.group(1)), product=line.group(2).strip(),
                     price_per_gal=float(line.group(3)), fuel_amount=_m(line.group(4)),
                     other_amount=round(d["total"] - _m(line.group(4)), 2))
        bk = re.search(r"BOOKING\s*#?\s*:\s*((?:[CAT]\s*){3}[\d\s]{8,12})", blk)
        if bk:
            d["booking_no"] = re.sub(r"\s", "", bk.group(1))[:11]
        t = re.search(r"CONTAINER\s*#?\s*:\s*([A-Z]{4}\d{7})", blk)
        if t:
            d["tank_no"] = t.group(1)
        out[d["invoice_no"]] = d
    return list(out.values()) or _port_consolidated_ocr(text)


def _port_consolidated_ocr(text):
    out = []
    for blk in re.split(r"(?=portconsolidated\.com\s+INVOICE)", text, flags=re.I)[1:]:
        inv = re.search(r"DEL\. TICKET[^\n]*\n\s*(\d{7})", blk)
        if not inv:
            continue
        head = text[:text.find(blk)][-400:]           # el total suele quedar justo antes del bloque
        total = re.findall(r"\$\s*" + MONEY, head) or re.findall(r"\$\s*" + MONEY, blk[:1500])
        d = {"vendor": "Port Consolidated", "invoice_no": inv.group(1), "concept": "fuel", "status": "final",
             "notes": "PDF escaneado (OCR) - verificar"}
        if total:
            d["total"] = _m(total[-1] if head.count("$") else total[0])
        bk = re.search(r"BOOKING\s*#?\s*:?\s*(CAT\d{8})", blk, re.I)
        if bk:
            d["booking_no"] = bk.group(1)
        t = re.search(r"CONTAINER\s*#?\s*:?\s*([A-Z]{4}\d{7})", blk, re.I)
        if t:
            d["tank_no"] = t.group(1)
        g = re.search(r"TOTALS\s*\[GALS\]\s*:\s*(\d{4,5})|TOTAL\s+(\d{4,5})\s+\d{4,5}", blk)
        if g:
            d["gallons"] = float(g.group(1) or g.group(2))
        d["product"] = detect_fuel(blk)
        if "total" in d:
            out.append(d)
    return out


# ------------------------------------------------------------------ NueFuel (provisional)
def _nuefuel(text):
    if "NueFuel" not in text or not re.search(r"INVOICE:\s*KML", text):
        return []
    inv = re.search(r"INVOICE:\s*(KML\s*\d+R?)", text)
    amounts = [_m(x) for x in re.findall(r"\$\s*" + MONEY, text)]
    if not inv or not amounts:
        return []
    provisional = "PROVISIONAL" in text.upper()
    sub = re.search(r"SUBTOTAL\s+\$\s*" + MONEY, text)
    d = {"vendor": "NueFuel", "invoice_no": re.sub(r"\s", "", inv.group(1)),
         "total": _m(sub.group(1)) if sub else max(amounts),
         "concept": "fuel", "status": "provisional" if provisional else "final"}
    dep = re.search(r"DEPOSIT\s+\(\$\s*" + MONEY + r"\)", text)
    gals = re.findall(r"([\d,]{5,})\s*US GALLONS", text)
    notes = []
    if dep:
        notes.append(f"depósito requerido ${dep.group(1)}")
    if gals:
        notes.append("galones: " + " + ".join(gals))
    d["notes"] = "; ".join(notes) or None
    rate = re.search(r"\*\s*\$\s*(\d\.\d{2,4})\b", text)
    if rate:
        d["price_per_gal"] = float(rate.group(1))
    return [d]


# ------------------------------------------------------------------ Ushine Trucking (drayage IBC, en español)
_MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9,
          "oct": 10, "nov": 11, "dic": 12}


def _ushine(text):
    if "UshineTrucking" not in text.replace(" ", ""):
        return []
    out = {}
    for blk in re.split(r"(?=FACTURA\s*\n\s*Ushine)", text)[1:]:
        num = re.search(r"N[uú]mero:\s*(FAC\d+)", blk)
        total = re.search(r"\bTOTAL:\s*\$\s*" + MONEY, blk)
        if not num or not total:
            continue
        d = {"vendor": "Ushine Trucking", "invoice_no": num.group(1), "total": _m(total.group(1)),
             "concept": "drayage", "status": "final"}
        f = re.search(r"Fecha:\s*([a-z]{3})[a-z]*\.?\s*(\d{1,2}),?\s*(\d{4})", blk, re.I)
        if f and f.group(1).lower() in _MESES:
            d["invoice_date"] = f"{f.group(3)}-{_MESES[f.group(1).lower()]:02d}-{int(f.group(2)):02d}"
        line = re.search(r"Booking#([^\n]*)", blk)
        if line:
            lst = list(dict.fromkeys(re.findall(r"CAT\d{8}|9\d{6}[A-Z]", line.group(1))))
            d["booking_no"] = lst[0] if len(lst) == 1 else None
            d["notes"] = "bookings " + ", ".join(lst) if lst else "booking pendiente"
        paid = re.search(r"PAGADA:\s*\$\s*" + MONEY, blk)
        if paid and _m(paid.group(1)):
            d["notes"] = (d.get("notes") or "") + f"; pagado según factura ${paid.group(1)}"
        out[d["invoice_no"]] = d
    return list(out.values())


# ------------------------------------------------------------------ Crowley (flete marítimo)
def _crowley(text):
    out = {}
    for blk in re.split(r"(?=Crowley Latin America Services)", text)[1:]:
        inv = re.search(r"Invoice:\s*(CLI\d+)", blk)
        total = re.search(r"Total in USD\s*\$\s*" + MONEY, blk)
        if not inv or not total:
            continue
        d = {"vendor": "Crowley", "invoice_no": inv.group(1), "total": _m(total.group(1)),
             "concept": "freight", "status": "final"}
        for key, pat in (("bl_no", r"Bill of Lading\s*:\s*(\S+)"), ("booking_no", r"Booking\s*:\s*(CAT\d+)")):
            m = re.search(pat, blk)
            if m:
                d[key] = m.group(1)
        dt = re.search(r"Invoice Date\s*:\s*([A-Z][a-z]{2} \d{1,2}, \d{4})", blk)
        if dt:
            d["invoice_date"] = _date_text(dt.group(1))
        fr = re.search(r"Ocean Freight[^\n]*\$\s*" + MONEY, blk)
        if fr:
            d["freight_amount"] = _m(fr.group(1))
            d["other_amount"] = round(d["total"] - d["freight_amount"], 2)
        vv = re.search(r"Vessel/Voyage:\s*([^\n]+)", blk)
        n = len(set(re.findall(r"\b([A-Z]{3}U\d{7})\b", blk)))
        d["notes"] = "; ".join(x for x in (f"vessel {vv.group(1).strip()}" if vv else "",
                                           f"{n} tanques" if n else "") if x) or None
        if n:
            d["iso_count"] = n
        bal = re.search(r"Balance Due\s*\$\s*" + MONEY, blk)
        if bal and _m(bal.group(1)) < d["total"]:
            d["notes"] = (d["notes"] or "") + f"; saldo según Crowley ${bal.group(1)}"
        out[d["invoice_no"]] = d
    return list(out.values())


READERS = [_ascent, _ccs, _port_consolidated, _nuefuel, _ushine, _crowley]


def parse(text: str) -> list[dict]:
    text = text.replace("\r\n", "\n")
    return [item for reader in READERS for item in reader(text)]
