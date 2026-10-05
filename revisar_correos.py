"""Lista los correos nuevos del inbox para que la rutina decida cuáles hay que responder.

Solo lectura: no marca como leído, no responde, no cambia etiquetas.
Uso:  python revisar_correos.py [minutos]      (por defecto, los últimos 70 minutos)
"""
import base64
import email
import re
import sys
import time
from email.utils import parsedate_to_datetime

from kf.email_ingest import _body_and_attachments, _decode
from kf.gmail_api import _execute, service

CUENTA = "accounting@katapulk.com"
MAX_CUERPO = 1500


def _header(msg, name):
    return _decode(msg.get(name) or "")


def _limpiar(texto):
    """Quita restos de CSS/HTML de los correos de bancos y publicidad y junta los espacios."""
    texto = re.sub(r"\{[^{}]*\}", " ", texto)
    texto = re.sub(r"<[^>]+>|@media[^\n]*|#outlook a", " ", texto)
    return re.sub(r"[ \t]*\n[\s]*\n+", "\n\n", re.sub(r"[ \t]+", " ", texto)).strip()


def main(minutos=70):
    svc = service()
    desde = int(time.time()) - minutos * 60
    q = f"in:inbox after:{desde} -from:{CUENTA}"
    ids, token = [], None
    while True:
        resp = _execute(svc.users().messages().list(userId="me", q=q, pageToken=token, maxResults=100))
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token:
            break
    if not ids:
        print("SIN_CORREOS_NUEVOS")
        return
    print(f"{len(ids)} correo(s) nuevo(s) desde hace {minutos} minutos\n")
    for mid in reversed(ids):                       # del más viejo al más nuevo
        meta = _execute(svc.users().messages().get(userId="me", id=mid, format="minimal"))
        raw = _execute(svc.users().messages().get(userId="me", id=mid, format="raw"))["raw"]
        msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
        body, attachments = _body_and_attachments(msg)
        try:
            fecha = parsedate_to_datetime(msg["Date"]).isoformat()
        except Exception:
            fecha = msg["Date"]
        # ¿accounting ya respondió en el hilo después de este correo?
        hilo = _execute(svc.users().threads().get(userId="me", id=meta["threadId"], format="metadata",
                                                  metadataHeaders=["From"]))
        recibido = int(meta["internalDate"])
        respondido = any(
            int(m["internalDate"]) > recibido and "SENT" in m.get("labelIds", [])
            and CUENTA in next((h["value"].lower() for h in m["payload"]["headers"] if h["name"] == "From"), "")
            for m in hilo.get("messages", []))
        print("=" * 70)
        print(f"Fecha:   {fecha}")
        print(f"De:      {_header(msg, 'From')}")
        print(f"Para:    {_header(msg, 'To')}")
        print(f"CC:      {_header(msg, 'Cc')}")
        print(f"Asunto:  {_header(msg, 'Subject')}")
        print(f"Mensajes en el hilo: {len(hilo.get('messages', []))} | "
              f"accounting ya respondió después: {'SÍ' if respondido else 'NO'}")
        if attachments:
            print(f"Adjuntos: {', '.join(attachments)}")
        print("--- cuerpo (inicio) ---")
        print(_limpiar(body)[:MAX_CUERPO])
        print()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 70)
