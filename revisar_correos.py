"""Lista los correos nuevos del inbox para que la rutina decida cuáles hay que responder.

Solo lectura: no marca como leído, no responde, no cambia etiquetas.
Solo usa la biblioteca estándar de Python (no hay que instalar nada). En la nube, el proxy
inyecta los secretos al pedir el token a oauth2.googleapis.com; aquí solo va KF_GOOGLE_CLIENT_ID.
Uso:  python3 revisar_correos.py [minutos]      (por defecto, los últimos 70 minutos)
"""
import base64, json, os, re, sys, time, urllib.parse, urllib.request
CUENTA = "accounting@katapulk.com"
def token():
    data = urllib.parse.urlencode({"client_id": os.environ["KF_GOOGLE_CLIENT_ID"], "grant_type": "refresh_token"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data), timeout=30) as r:
        return json.loads(r.read())["access_token"]
TK = token()
def api(path, **q):
    url = "https://gmail.googleapis.com/gmail/v1/users/me/" + path + ("?" + urllib.parse.urlencode(q, doseq=True) if q else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + TK}), timeout=60) as r:
        return json.loads(r.read())
def texto(p):
    plano, html = [], []
    def walk(x):
        d = x.get("body", {}).get("data")
        if d and x.get("mimeType") == "text/plain": plano.append(base64.urlsafe_b64decode(d).decode("utf-8", "replace"))
        if d and x.get("mimeType") == "text/html": html.append(base64.urlsafe_b64decode(d).decode("utf-8", "replace"))
        for y in x.get("parts", []): walk(y)
    walk(p)
    t = "\n".join(plano) or re.sub(r"<[^>]+>", " ", re.sub(r"(?s)<(style|script)[^>]*>.*?</\1>", " ", "\n".join(html)))
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\xa0\u034f\u200b\u200c\ufeff]+", " ", t)).strip()
minutos = int(sys.argv[1]) if len(sys.argv) > 1 else 70
q = f"in:inbox after:{int(time.time()) - minutos * 60} -from:{CUENTA}"
ids = [m["id"] for m in api("messages", q=q, maxResults=100).get("messages", [])]
if not ids: print("SIN_CORREOS_NUEVOS"); sys.exit()
for mid in reversed(ids):
    m = api("messages/" + mid, format="full")
    h = {x["name"].lower(): x["value"] for x in m["payload"]["headers"]}
    hilo = api("threads/" + m["threadId"], format="metadata", metadataHeaders="From").get("messages", [])
    resp = any(int(x["internalDate"]) > int(m["internalDate"]) and "SENT" in x.get("labelIds", []) for x in hilo)
    print("=" * 60)
    print(f"message_id: {mid} | thread_id: {m['threadId']}")
    for k in ("date", "from", "to", "cc", "subject"): print(f"{k}: {h.get(k, '')}")
    print(f"mensajes en el hilo: {len(hilo)} | accounting ya respondio despues: {'SI' if resp else 'NO'}")
    print(texto(m["payload"])[:1500]); print()
