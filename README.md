# Katapulk Fuel Ops

Control de bookings, ISO Tanks, IBC Totes, invoices y pagos de combustible hacia Cuba.
Diseñado para ser **simple de mantener**: Python + SQLite (un solo archivo de base de datos),
sin servidores. Cada módulo es un archivo pequeño que se puede cambiar sin tocar los demás.

## Arquitectura

```
 Gmail ──IMAP──▶ email_ingest.py ──▶ parsers.py (regex: bookings, B/L, tanques, montos, fechas)
                        │                          │
                        ▼                          ▼
                 tabla emails              ops.py (reglas de negocio)
                 (pendientes de revisión)          │
                                                   ▼
  cli.py (entrada manual) ───────────────▶  SQLite  data/katapulk_fuel.db
                                            ├─ estado actual: bookings, iso_tanks, ibc_lots,
                                            │                 invoices, payments, payment_allocations
                                            ├─ historial:     events (cada cambio, con su origen)
                                            │                 tank_trips (cada viaje de cada tanque)
                                            └─ vistas:        v_invoice_status, v_payment_status,
                                                              v_vendor_balance
                                                   │
                         ┌─────────────────────────┼─────────────────────┐
                         ▼                         ▼                     ▼
                  alerts.py (reglas)       reports.py (reporte    dashboard.py
                                           diario Markdown)       (Streamlit)
```

| Archivo | Qué hace |
|---|---|
| `schema.sql` | Todas las tablas y vistas. Para agregar un campo, se agrega aquí. |
| `kf/db.py` | Conexión y `upsert()`: guarda el cambio **y** lo registra en `events`. |
| `kf/ops.py` | Bookings, asignación de tanques, propagación de estados, retornos, reuso, IBC, invoices, pagos, aplicación automática de créditos. |
| `kf/parsers.py` | Clasificación de emails y extracción por expresiones regulares. |
| `kf/email_ingest.py` | Lee Gmail (solo lectura), aplica cambios seguros, guarda el resto para revisión. |
| `kf/alerts.py` | Una función por alerta. Nueva alerta = nueva función en `RULES`. |
| `kf/reports.py` | Métricas del dashboard y reporte diario. |
| `cli.py` | Todos los comandos. |
| `dashboard.py` | Dashboard web local. |

### Decisiones clave
- **Historial completo sin esfuerzo**: nunca se escribe directo en las tablas; todo pasa por `upsert()`, que anota en `events` el valor anterior, el nuevo y el origen (`manual`, `import`, `email:<message-id>`). Así se reconstruye la vida de cada tanque: `python cli.py history tank CRXU1234567`.
- **Ciclo del ISO Tank**: cada tramo (salida o retorno) es una fila en `tank_trips`. Al cambiar el status de un booking, sus tanques se actualizan solos:

  `available → loaded → in_transit_cuba → in_cuba → returning → in_us_hold → ready_pickup → (next_booking) → loaded ...`
- **Pagos vs invoices**: un pago puede cubrir varias invoices y una invoice puede pagarse con varios pagos (`payment_allocations`). El **crédito disponible = pago − invoices aplicadas** lo calcula la vista `v_payment_status`; nunca se guarda a mano, así no se desincroniza. `auto-apply` aplica primero por booking y después por antigüedad (FIFO).
- **IBC Totes por lote** (booking + capacidad), no uno por uno. Galones = totes × capacidad (columna calculada).
- **Emails**: lo que el sistema reconoce con seguridad se aplica solo (cambios de vessel/voyage/cutoff en bookings existentes, A/N, release, hazmat aprobado, invoices con vendor+número+total). Lo demás queda en `emails` con `processed=0` y aparece como alerta.

## Instalación (Windows)

1. Instalar Python 3.11+ desde python.org (marcar "Add Python to PATH").
2. En esta carpeta:
   ```bash
   pip install -r requirements.txt
   ```
3. Probar con tus ejemplos:
   ```bash
   python -m unittest discover tests
   ```
   ```bash
   python seed_example.py
   ```
4. Gmail: activar verificación en 2 pasos, crear una *App password*, copiar `.env.example` como `.env` y completarlo.

## Conectar Gmail con OAuth (si la cuenta no permite contraseñas de aplicación)

1. https://console.cloud.google.com → crear proyecto "Katapulk Fuel".
2. APIs y servicios → Biblioteca → **Gmail API** → Habilitar.
3. Pantalla de consentimiento de OAuth → tipo **Interno** → nombre de la app y correo de soporte.
4. Credenciales → Crear credenciales → ID de cliente OAuth → tipo **App de escritorio** → Descargar JSON.
5. Guardar ese archivo como `credentials.json` en esta carpeta.
6. `python cli.py email --limit 5` → se abre el navegador, entrar con accounting@katapulk.com y aceptar (solo lectura).

Con `credentials.json` presente el sistema usa la API; sin él, usa IMAP con la contraseña de `.env`.

## Uso diario

```bash
python cli.py email
```
```bash
python cli.py alerts
```
```bash
streamlit run dashboard.py
```

Ver todos los comandos con ejemplos al inicio de `cli.py`. `run_daily.ps1` corre la rutina completa y se puede programar en el Programador de tareas de Windows.

## Cómo crecer (módulos siguientes, en orden sugerido)

1. **Ajustar parsers con emails reales** — guardar 10–20 emails de cada proveedor como `.eml`, probarlos con `python cli.py email-file x.eml` y agregar cada caso a `tests/`.
2. **Invoices en PDF con IA** — para facturas con formato variable (Triton, MidTex, NueFuel), enviar el texto del PDF a un modelo de lenguaje con un esquema JSON fijo (vendor, invoice_no, booking, galones, precio/gal, freight, total) y guardar el resultado con `ops.save_invoice(..., notes="IA - verificar")`.
3. **Aviso diario por email/WhatsApp** con el reporte y las alertas ALTA.
4. **Importar desde Excel** los bookings/tanques históricos que ya tengas.
5. **Multiusuario**: si más personas deben usarlo, migrar SQLite → PostgreSQL (el SQL es casi el mismo) y publicar el dashboard en la red interna.
