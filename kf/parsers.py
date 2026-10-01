"""Clasificación y extracción de datos desde texto de emails (y texto de PDFs).

Todo son expresiones regulares: fácil de ajustar cuando un proveedor cambia el formato.
Cada formato real está cubierto por un caso en tests/test_real_emails.py; si un email nuevo
no se reconoce, agrega el caso allí y ajusta el patrón aquí.
"""
import re

OWN_DOMAIN = "katapulk.com"

# Asuntos que genera la propia empresa y no deben procesarse (alertas internas, etc.)
SKIP_SUBJECTS = [r"^\[URGENTE\] Agente ISO"]

# ------------------------------------------------------------------ patrones
# Crowley: CAT40268984, a veces con sufijo de sub-booking: CAT40249785002
CROWLEY_BK = r"CAT\d{8}(?:\d{3})?"
# Seaboard: 9224051A, HOU9224051A, BK#9253089A, "9212404 A"
SEABOARD_BK = r"(?:HOU|MIA)?9\d{6}\s?[A-Z]"

PATTERNS = {
    # sin \b: "CAT40262737_10 ISO" también cuenta (el "_" no corta la palabra en regex)
    "booking": re.compile(rf"(?<![A-Za-z0-9])(?:{CROWLEY_BK}|{SEABOARD_BK})(?![A-Za-z0-9])"),
    # Crowley: CWPS26265124 / CWPN26189608 ; Seaboard: SMLU9196581A
    "bl": re.compile(r"\b(CW[A-Z]{2}\d{8}|SMLU\d{7}[A-Z])\b"),
    # ISO 6346: 3 letras + U + 6 dígitos + dígito control (a veces lo omiten: TUSU132180)
    "tank": re.compile(r"\b([A-Z]{3}U)\s?(\d{6})\s?(\d?)\b"),
    "invoice_no": re.compile(
        r"\binvoice\s*(?:number|no\.?|num\.?|#)?\s*[:#]?\s*([A-Z]{2,4}[\s\-]?\d{3,}[A-Z]?|\d{4,})\b", re.I),
    "amount": re.compile(
        r"(?:total(?: due| amount| payment| of)?|a total of|amount due|balance due|transaction amount|in the amount of|amount|importe)"
        r"\s*:?\s*(?:USD\s*)?\(?\$?\s*(\d[\d,]*\.\d{2})", re.I),
    "wire": re.compile(r"\b(?:bank reference|fed\s*ref(?:erence)?|wire\s*(?:no\.?|#|number|ref)?)\s*[:#]?\s*([A-Z0-9]{8,})", re.I),
    "vessel_voyage": re.compile(r"(?:Vessel\s*/\s*Voyage|Buque\s*/\s*Viaje)\s*:\s*(?:([A-Z0-9][A-Z0-9 .\-]*?)\s*/\s*)?([A-Z]*\d[A-Z0-9\-]*)", re.I),
    "rolled": re.compile(r"rolled to\s*:?\s*(CUB\d{4})", re.I),
    "crowley_voyage": re.compile(r"\b(CUB\d{4})\b"),
    # "Voyage: 245N", "Voy 557", "Seaboard Ranger V.556"
    "voyage_generic": re.compile(r"(?:\bvoy(?:age)?\.?\s*(?:no\.?|#)?\s*[:\-]?\s*|\bV\.\s?)([A-Z]{0,4}\d[A-Z0-9]{0,8})\b", re.I),
    "vessel_named": re.compile(r"\b(?:vessel|buque)\s*(?:name)?\s*:\s*([A-Z][A-Za-z0-9 .\-]{2,30}?)\s*(?:\n|/|voy|$)", re.I),
    "cutoff": re.compile(r"\b(?:cut\s*-?\s*off|cy\s*cut|doc\s*cut)[^:\n]*[:\-]\s*(" + r"[0-9]{1,4}[/\-][0-9]{1,2}[/\-][0-9]{2,4}(?:\s+[0-9]{1,2}:[0-9]{2})?"
                         r"|[A-Z][a-z]{2}\.? \d{1,2},? \d{4})", re.I),
    "etd": re.compile(r"\bETD\s*[:\-]?\s*([0-9]{1,4}[/\-][0-9]{1,2}[/\-][0-9]{2,4}|[A-Z][a-z]{2}\.? \d{1,2},? \d{4})", re.I),
    "eta": re.compile(r"\bETA\s*[:\-]?\s*([0-9]{1,4}[/\-][0-9]{1,2}[/\-][0-9]{2,4}|[A-Z][a-z]{2}\.? \d{1,2},? \d{4})", re.I),
    "pol": re.compile(r"(?:Puerto de Carga|Port of Loading|POL)\s*:\s*([^\n]+)", re.I),
    "pod": re.compile(r"(?:Puerto de Descarga|Port of Discharge|POD)\s*:\s*([^\n]+)", re.I),
    "client": re.compile(r"\bCliente\s*:\s*([^\n]+)", re.I),
    "gallons": re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:gal(?:lon)?s?)\b", re.I),
    "iso_count": re.compile(r"\b(\d{1,3})\s*(?:x\s*)?ISO\s*tanks?\b", re.I),
    "container_count": re.compile(r"\b(\d{1,3})\s*x\s*40\s*'?\s*(?:DRY|HC|units?)", re.I),
    "tote_count": re.compile(r"\b(\d[\d,]{0,5})\s*(?:IBC\s*)?totes?\b", re.I),
    "tote_capacity": re.compile(r"\b(275|300|330)\s*(?:gal|gallons?)\b", re.I),
    # Líneas de reparto de un pago (pueden ocupar varias líneas):
    #   "- $24,480.00 – Towards US-Reentry/Return Booking CAT40268984 / B/L\n   CWPN26189608 – 18 ISO Tanks"
    #   "$38,295.00\tto\tCAT40262737_10 ISO Tanks Gasoline Fuel"
    "alloc_line": re.compile(
        r"^[ \t]*(?:[-•][ \t]*)?\$\s*(\d[\d,]*\.\d{2})\s*(?:[–\-]\s*(?:towards|for|para)|to|para)\s+"
        r"(.+?)(?=\n[ \t]*(?:[-•][ \t]*)?\$|\n[ \t]*\n|\Z)", re.I | re.M | re.S),
}

# Palabras clave -> categoría. Un email puede tener varias.
CATEGORIES = {
    "invoice":          [r"\binvoice\b", r"\bfactura\b"],
    "payment_request":  [r"payment (?:request|due|required)", r"please (?:remit|pay)", r"balance due", r"solicitud de pago",
                         r"credit hold"],
    "payment_confirm":  [r"payment (?:has been )?received", r"payment confirmation", r"wire (?:sent|confirmation|transfer)",
                         r"remittance", r"pago (?:recibido|enviado|realizado)"],
    "payment_sent":     [r"payment confirmation in the amount of", r"payment allocation"],
    "booking_new":      [r"booking (?:confirmation|confirmed)", r"new booking", r"n[uú]meros? de reserva",
                         r"confirmaciones adjuntas"],
    "booking_change":   [r"(?:vessel|voyage|cut-?off|schedule|sailing)\s*(?:change|update|revised|amend)",
                         r"\brolled\b", r"\bamend(?:ed|ment)\b", r"move (?:it )?to a later sailing", r"reactivate"],
    "bl":               [r"bill of lading", r"\bB/L\b", r"draft BL", r"\bOBL\b", r"non-negotiable"],
    "shipping_docs":    [r"documentaci[oó]n de embarque"],
    "arrival_notice":   [r"arrival notice", r"\bA/N\b"],
    "release":          [r"\breleased\b", r"freight release", r"container release", r"available for pick ?up"],
    "hazmat":           [r"\bhazmat\b", r"\bIMOs?\b", r"dangerous goods", r"\bUN ?120[23]\b"],
    "doc_problem":      [r"missing (?:document|doc|info)", r"discrepan", r"\bon hold\b", r"rejected", r"incomplete",
                         r"\bcorrect(?:ion)?\b", r"please correct"],
    "customs":          [r"\bcustoms\b", r"\bCBP\b", r"\bISF\b", r"brokerage"],
    "fuel_quote":       [r"\bquote\b", r"cotizaci[oó]n", r"fuel order"],
}

# Palabras que por sí solas bastan para considerar el email del negocio.
# ("invoice" o "payment" solos NO bastan: hay facturas legales, de software, etc.)
STRONG_KEYWORDS = [
    "fuel", "gasoline", "gasolina", "diesel", "diésel", "ulsd", "iso tank", "isotank", "ibc", "tote",
    "crowley", "seaboard", "triton", "midtex", "nuefuel", "port consolidated", "industrial packing",
    "world fuel", "wfscorp", "bill of lading", "arrival notice", "hazmat", "emserpet", "jacintoport",
]

# El orden importa: se busca primero en el remitente, luego en el asunto, luego en el cuerpo.
VENDOR_HINTS = {
    "CCS (Crowley demurrage)": ["ccspr", "carrier credit services"],
    "Ushine Trucking": ["ushine"],
    "Crowley": ["crowley"],
    "Seaboard": ["seaboard", "smlines", "jacintoport"],
    "Triton Energy": ["triton"],
    "Port Consolidated": ["portconsolidated", "port consolidated"],
    "MidTex": ["midtex"],
    "NueFuel": ["nuefuel"],
    "Industrial Packing": ["industrialpacking", "industrial packing"],
    "World Fuel": ["wfscorp", "world fuel", "ascent"],
}

# Marcadores donde empieza el texto citado de una respuesta.
_REPLY_MARKERS = re.compile(
    r"^\s*(?:-{5,}\s*Original Message\s*-{5,}|_{10,}|On [^\n]{5,400}?\n?[^\n]{0,200}?wrote:|"
    r"El [^\n]{5,400}?\n?[^\n]{0,200}?escribi[oó]:|"
    r"From: .+\n\s*Sent: |De: .+\n\s*(?:Enviado|Sent): |Begin forwarded message:)",
    re.I | re.M)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def latest_part(body: str) -> str:
    """Solo el mensaje nuevo, sin el historial citado (evita tomar datos viejos del hilo)."""
    m = _REPLY_MARKERS.search(body)
    return body[:m.start()] if m and m.start() > 0 else body


def is_own(sender: str) -> bool:
    return OWN_DOMAIN in (sender or "").lower()


def should_skip(subject: str) -> bool:
    return any(re.search(p, subject or "", re.I) for p in SKIP_SUBJECTS)


def is_relevant(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in STRONG_KEYWORDS) or bool(
        PATTERNS["booking"].search(text) or PATTERNS["bl"].search(text))


def classify(subject: str, body: str) -> list[str]:
    text = f"{subject}\n{latest_part(body.replace(chr(13), '').replace('*', ''))}"
    return [cat for cat, pats in CATEGORIES.items() if any(re.search(p, text, re.I) for p in pats)]


def detect_vendor(sender: str, subject: str, body: str = "", to: str = "") -> str | None:
    """Remitente externo > asunto > cuerpo > destinatarios (para emails que enviamos nosotros)."""
    s = (sender or "").lower()
    if not is_own(s):
        for vendor, hints in VENDOR_HINTS.items():
            if any(h.replace(" ", "") in s for h in hints):
                return vendor
    for text in (subject.lower(), body.lower(), (to or "").lower()):
        for vendor, hints in VENDOR_HINTS.items():
            if any(h in text for h in hints):
                return vendor
    return None


def detect_fuel(text: str) -> str | None:
    g = bool(re.search(r"\bgasoline\b|\bgasolina\b|\bgasol\b|\bUN ?1203\b|\bULG\b|\bRUG\b|\bE10\b", text, re.I))
    d = bool(re.search(r"\bdiesel\b|\bdi[eé]sel\b|\bULSD\b|\bUN ?1202\b", text, re.I))
    if g and not d:
        return "gasoline"
    if d and not g:
        return "diesel"
    return None                                          # ambos o ninguno: revisar a mano


def norm_booking(b: str) -> str:
    return re.sub(r"\s", "", b).upper()


def to_iso_date(s: str | None) -> str | None:
    """'10/03/2026 14:00' -> '2026-10-03 14:00' (formato USA mes/día); 'Oct 02, 2026' -> '2026-10-02'."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})", s)
    if m and m.group(1).lower() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    m = re.match(r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})(.*)", s)
    if m:
        y, mo, d, rest = m.groups()
    else:
        m = re.match(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})(.*)", s)
        if not m:
            return s
        mo, d, y, rest = m.groups()
        y = y if len(y) == 4 else "20" + y
    return f"{y}-{int(mo):02d}-{int(d):02d}{rest.rstrip()}"


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def _first(key, *texts):
    for t in texts:
        m = PATTERNS[key].search(t)
        if m:
            return m
    return None


def extract(subject: str, body: str, sender: str = "", to: str = "") -> dict:
    """Devuelve todo lo que pudo reconocer. Campos no encontrados no aparecen.

    Los datos se toman del asunto + el mensaje nuevo (sin historial citado).
    Los números de tanque se buscan en todo el hilo, porque suelen venir en mensajes anteriores.
    """
    body = body.replace("\r\n", "\n").replace("*", "")     # Gmail marca negritas con *...*
    top = latest_part(body)
    head = f"{subject}\n{top}"
    out: dict = {}

    bookings = list(dict.fromkeys(norm_booking(b) for b in PATTERNS["booking"].findall(head)))
    if bookings:
        out["bookings"] = bookings

    bls = list(dict.fromkeys(PATTERNS["bl"].findall(head)))
    if bls:
        out["bls"] = bls

    tanks = list(dict.fromkeys("".join(t) for t in PATTERNS["tank"].findall(f"{subject}\n{body}")))
    if tanks:
        out["tanks"] = tanks

    # voyage: "rolled to" manda sobre todo; luego el campo etiquetado; luego un CUBxxxx del mensaje nuevo
    m = PATTERNS["rolled"].search(top)
    if m:
        out["voyage"] = m.group(1)
        out["rolled"] = True
    else:
        m = PATTERNS["vessel_voyage"].search(top)
        if m:
            if m.group(1):
                out["vessel"] = m.group(1).strip()
            out["voyage"] = m.group(2).strip()
        else:
            m = _first("crowley_voyage", top, subject) or _first("voyage_generic", top)
            if m:
                out["voyage"] = m.group(1)
    if "vessel" not in out:
        m = PATTERNS["vessel_named"].search(top)
        if m:
            out["vessel"] = m.group(1).strip()

    m = _first("invoice_no", subject, top)
    if m:
        out["invoice_no"] = re.sub(r"\s", "", m.group(1)).upper()

    for key in ("wire", "cutoff", "etd", "eta", "pol", "pod", "client"):
        m = PATTERNS[key].search(top)
        if m:
            v = m.group(1).strip()
            out[key] = to_iso_date(v) if key in ("cutoff", "etd", "eta") else v

    amounts = [_money(a) for a in PATTERNS["amount"].findall(head)]
    if amounts:
        out["amount"] = max(amounts)         # el "total" suele ser el mayor

    for key in ("iso_count", "container_count", "tote_capacity"):
        m = PATTERNS[key].search(head)
        if m:
            out[key] = int(m.group(1).replace(",", ""))
    totes = [int(x.replace(",", "")) for x in PATTERNS["tote_count"].findall(head) if x.replace(",", "")]
    if totes:
        out["tote_count"] = max(totes)       # "25 totes each ... Total 300 Totes" -> 300

    m = PATTERNS["gallons"].search(head)
    if m and "tote_capacity" not in out:
        out["gallons"] = _money(m.group(1))

    allocs = []
    for amt, desc in PATTERNS["alloc_line"].findall(top):
        bk = PATTERNS["booking"].search(desc)
        allocs.append({"amount": _money(amt), "booking_no": norm_booking(bk.group(0)) if bk else None,
                       "description": re.sub(r"\s+", " ", desc).strip()})
    if allocs:
        out["allocations"] = allocs

    fuel = detect_fuel(head)
    if fuel:
        out["fuel_type"] = fuel
    vendor = detect_vendor(sender, subject, top, to)
    if vendor:
        out["vendor"] = vendor
    return out


def parse_return_lines(text: str) -> list[dict]:
    """Lee bloques tipo:
        CAT40268984 / CWPN26189608
        18 ISO Tanks
        Gasolina
    """
    rows = []
    blocks = re.split(rf"(?=(?<![A-Za-z0-9])(?:{CROWLEY_BK}|{SEABOARD_BK})(?![A-Za-z0-9]))", text)
    for blk in blocks:
        b = PATTERNS["booking"].search(blk)
        if not b:
            continue
        bl = PATTERNS["bl"].search(blk)
        n = re.search(r"\b(\d{1,3})\s*ISO", blk, re.I)
        rows.append({
            "booking_no": norm_booking(b.group(0)),
            "bl_no": bl.group(1) if bl else None,
            "iso_count": int(n.group(1)) if n else None,
            "residue_type": detect_fuel(blk),
        })
    return rows
