"""Lectura de Gmail con la API oficial de Google (OAuth). Alternativa a IMAP cuando la cuenta
de empresa no permite contraseñas de aplicación.

Requiere credentials.json (cliente OAuth "Desktop app") en la raíz del proyecto.
La primera vez abre el navegador para autorizar; después guarda token.json y ya no pregunta.
Permiso pedido: solo lectura (gmail.readonly).
"""
import base64
import email
import os
import time
from email.utils import parsedate_to_datetime

from .db import ROOT
from .email_ingest import _body_and_attachments, _decode, _load_env

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS = ROOT / "credentials.json"
TOKEN = ROOT / "token.json"


def service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS.exists():
                raise SystemExit(f"Falta {CREDENTIALS.name}. Ver README: 'Conectar Gmail con OAuth'.")
            creds = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES).run_local_server(port=0)
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _execute(request, tries=6):
    """Reintenta cuando Google responde 'límite por minuto' (403/429), esperando cada vez más."""
    from googleapiclient.errors import HttpError
    for i in range(tries):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status not in (403, 429, 500, 503) or i == tries - 1:
                raise
            if e.resp.status == 403 and b"ateLimit" not in e.content and b"uota" not in e.content:
                raise
            time.sleep(min(60, 5 * 2 ** i))


def fetch(limit=200):
    """Mismo formato que email_ingest.fetch: (message_id, fecha, remitente, asunto, cuerpo, para)."""
    _load_env()
    query = os.environ.get("KF_GMAIL_QUERY", "newer_than:3d")
    svc = service()
    ids, token = [], None
    while len(ids) < limit:
        resp = _execute(svc.users().messages().list(userId="me", q=query, pageToken=token,
                                                    maxResults=min(500, limit - len(ids))))
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token:
            break
    for mid in reversed(ids):                       # del más viejo al más nuevo
        raw = _execute(svc.users().messages().get(userId="me", id=mid, format="raw"))["raw"]
        msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
        body, attachments = _body_and_attachments(msg)
        if attachments:
            body = "ATTACHMENTS: " + ", ".join(attachments) + "\n" + body
        try:
            received = parsedate_to_datetime(msg["Date"]).isoformat()
        except Exception:
            received = None
        yield (msg["Message-ID"] or f"gmail-{mid}", received,
               _decode(msg["From"]), _decode(msg["Subject"]), body, _decode(msg["To"]))
