"""Dashboard web local.  Ejecutar:  streamlit run dashboard.py"""
import pandas as pd
import streamlit as st

from kf import alerts, reports
from kf.db import connect, history, init_db

st.set_page_config(page_title="Katapulk Fuel Ops", layout="wide")
conn = connect()
init_db(conn)


def df(sql, *args):
    return pd.read_sql_query(sql, conn, params=args)


st.title("Katapulk — Combustible a Cuba")
m = reports.metrics(conn)

for sec, title in [("tanks", "ISO Tanks"), ("ops", "Operaciones"), ("money", "Dinero"), ("ibc", "IBC Totes")]:
    st.subheader(title)
    cols = st.columns(len(m[sec]))
    for col, (k, v) in zip(cols, m[sec].items()):
        col.metric(k, f"${v:,.0f}" if sec == "money" else f"{v:,}")

tab_pay, tab_alert, tab_bk, tab_tank, tab_money, tab_ibc, tab_mail, tab_hist = st.tabs(
    ["Pagos urgentes", "Acción requerida", "Bookings", "ISO Tanks", "Pagos / Invoices", "IBC Totes", "Emails", "Historial"])

with tab_pay:
    from kf import pagos
    items = pagos.urgent_payments(conn)
    to_pay = [i for i in items if i["tipo"] != "CRÉDITO"]
    creds = [i for i in items if i["tipo"] == "CRÉDITO"]
    st.markdown(f"**{pagos.headline(conn)}**")
    colors = {"BLOQUEO": "🔴", "EMBARQUE": "🔴", "DEPÓSITO": "🟠", "COBRO": "🟡", "SALDO": "⚪"}
    for n, i in enumerate(to_pay, 1):
        monto = f" — ${i['monto']:,.2f}" if i["monto"] else ""
        with st.expander(f"{colors.get(i['tipo'], '')} {n}. {i['tipo']} · {i['proveedor']}{monto}", expanded=n <= 3):
            st.write(f"**Por qué:** {i['motivo']}")
            st.write(f"**Qué hacer:** {i['accion']}")
            if i["ref"]:
                st.caption(f"Ref: {i['ref']}")
    if creds:
        st.markdown("**Créditos a favor (no pagar de más)**")
        st.dataframe(pd.DataFrame([{"Proveedor": c["proveedor"], "Crédito": c["monto"], "Detalle": c["motivo"]}
                                   for c in creds]), hide_index=True, use_container_width=True)

with tab_alert:
    a = pd.DataFrame(alerts.all_alerts(conn))
    if a.empty:
        st.success("Nada pendiente")
    else:
        st.dataframe(a, use_container_width=True, hide_index=True)

with tab_bk:
    status = st.multiselect("Status", ["active", "loaded", "empty", "shipped", "returning", "arrived", "closed", "blocked"],
                            default=["active", "loaded", "shipped", "returning", "blocked"])
    q = "SELECT * FROM bookings"
    d = df(q)
    st.dataframe(d[d.status.isin(status)] if status else d, use_container_width=True, hide_index=True)

with tab_tank:
    st.dataframe(df("SELECT tank_no, owner, fuel_type, fill_state, status, location, outbound_booking, return_booking,"
                    " next_booking, load_date, departure_date, cuba_arrival_date, us_return_date FROM iso_tanks"),
                 use_container_width=True, hide_index=True)
    st.caption("Por estado")
    st.bar_chart(df("SELECT status, COUNT(*) n FROM iso_tanks GROUP BY status").set_index("status"))

with tab_money:
    st.markdown("**Pagos** (crédito = pago − invoices aplicadas)")
    st.dataframe(df("SELECT * FROM v_payment_status ORDER BY pay_date DESC"), use_container_width=True, hide_index=True)
    st.markdown("**Invoices**")
    st.dataframe(df("SELECT * FROM v_invoice_status ORDER BY invoice_date DESC"), use_container_width=True, hide_index=True)
    st.markdown("**Balance por proveedor**")
    st.dataframe(df("SELECT * FROM v_vendor_balance"), use_container_width=True, hide_index=True)

with tab_ibc:
    st.dataframe(df("SELECT l.*, b.container_count, b.vessel, b.voyage, b.cutoff, b.status booking_status"
                    " FROM ibc_lots l JOIN bookings b USING(booking_no)"), use_container_width=True, hide_index=True)

with tab_mail:
    st.dataframe(df("SELECT received_at, sender, subject, categories, processed, extracted FROM emails"
                    " ORDER BY received_at DESC LIMIT 300"), use_container_width=True, hide_index=True)

with tab_hist:
    ent = st.selectbox("Tipo", ["tank", "booking", "payment", "invoice", "ibc"])
    key = st.text_input("Número (tanque, booking, id de pago...)").strip()
    if key:
        k = key.upper() if ent in ("tank", "booking", "ibc") else key
        st.dataframe(pd.DataFrame([dict(r) for r in history(conn, ent, k)]), use_container_width=True, hide_index=True)
        if ent == "tank":
            st.markdown("**Viajes del tanque**")
            st.dataframe(df("SELECT * FROM tank_trips WHERE tank_no = ? ORDER BY id", k), hide_index=True)
