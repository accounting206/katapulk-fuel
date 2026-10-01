-- =====================================================================
-- Katapulk Fuel Ops - esquema SQLite
-- Regla de oro: las tablas guardan el ESTADO ACTUAL; la tabla `events`
-- guarda TODO lo que pasó (historial completo, nunca se borra).
-- =====================================================================
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- catálogos
CREATE TABLE IF NOT EXISTS vendors (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,          -- 'Crowley', 'Triton Energy', ...
    kind        TEXT,                          -- carrier | fuel | broker | trucking | packing | other
    email_domain TEXT                          -- 'crowley.com' -> ayuda a clasificar emails
);

-- ---------------------------------------------------------------- bookings
CREATE TABLE IF NOT EXISTS bookings (
    id              INTEGER PRIMARY KEY,
    booking_no      TEXT NOT NULL UNIQUE,      -- CAT40268984 / HOU9224051A
    bl_no           TEXT,                      -- CWPN26189608
    carrier         TEXT,                      -- Crowley | Seaboard
    direction       TEXT NOT NULL DEFAULT 'outbound' CHECK (direction IN ('outbound','return')),
    equipment       TEXT NOT NULL DEFAULT 'iso' CHECK (equipment IN ('iso','ibc')),
    fuel_type       TEXT CHECK (fuel_type IN ('gasoline','diesel')),
    iso_count       INTEGER DEFAULT 0,
    container_count INTEGER DEFAULT 0,         -- contenedores 40' (IBC)
    fuel_vendor     TEXT,
    pol             TEXT,                      -- puerto de origen
    pod             TEXT,                      -- puerto de destino
    vessel          TEXT,
    voyage          TEXT,
    etd             TEXT,                      -- ISO date YYYY-MM-DD
    eta             TEXT,
    cutoff          TEXT,                      -- YYYY-MM-DD o YYYY-MM-DD HH:MM
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','loaded','empty','shipped','returning','arrived','closed','blocked')),
    docs_complete   INTEGER NOT NULL DEFAULT 0,
    hazmat_approved INTEGER NOT NULL DEFAULT 0,
    paid            INTEGER NOT NULL DEFAULT 0,
    -- campos propios de retornos
    residue_type    TEXT,                      -- residuo: gasoline | diesel
    arrival_notice  INTEGER NOT NULL DEFAULT 0,
    released        INTEGER NOT NULL DEFAULT 0,
    brokerage_done  INTEGER NOT NULL DEFAULT 0,
    customs_cleared INTEGER NOT NULL DEFAULT 0,
    pickup_available INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- ISO Tanks
CREATE TABLE IF NOT EXISTS iso_tanks (
    id              INTEGER PRIMARY KEY,
    tank_no         TEXT NOT NULL UNIQUE,      -- ej. CRXU1234567 (ISO 6346)
    owner           TEXT NOT NULL DEFAULT 'katapulk',   -- katapulk | crowley | world_fuel | <vendor>
    fuel_type       TEXT,
    fill_state      TEXT NOT NULL DEFAULT 'empty' CHECK (fill_state IN ('full','empty')),
    location        TEXT,                      -- 'Port Everglades', 'Mariel', 'Houston', 'in transit'
    status          TEXT NOT NULL DEFAULT 'available'
                    CHECK (status IN ('available','loading','loaded','in_transit_cuba','in_cuba',
                                      'returning','in_us_hold','ready_pickup','out_of_service')),
    client          TEXT,
    vendor          TEXT,
    outbound_booking TEXT,                     -- booking actual de salida
    return_booking   TEXT,                     -- booking actual de retorno
    next_booking     TEXT,                     -- próximo booking donde se reutilizará
    load_date       TEXT,
    departure_date  TEXT,
    cuba_arrival_date TEXT,
    us_return_date  TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Cada viaje de un tanque = una fila. Así el historial de ciclos queda completo.
CREATE TABLE IF NOT EXISTS tank_trips (
    id          INTEGER PRIMARY KEY,
    tank_no     TEXT NOT NULL REFERENCES iso_tanks(tank_no),
    booking_no  TEXT NOT NULL REFERENCES bookings(booking_no),
    leg         TEXT NOT NULL CHECK (leg IN ('outbound','return')),
    fuel_type   TEXT,
    gallons     REAL,
    start_date  TEXT,
    end_date    TEXT,
    UNIQUE (tank_no, booking_no)
);

-- ---------------------------------------------------------------- IBC Totes
-- Los totes se controlan por lote (booking + capacidad), no uno por uno.
CREATE TABLE IF NOT EXISTS ibc_lots (
    id              INTEGER PRIMARY KEY,
    booking_no      TEXT NOT NULL REFERENCES bookings(booking_no),
    tote_count      INTEGER NOT NULL,
    capacity_gal    INTEGER NOT NULL CHECK (capacity_gal IN (275,300,330)),
    total_gallons   INTEGER GENERATED ALWAYS AS (tote_count * capacity_gal) STORED,
    vendor          TEXT,
    fill_date       TEXT,
    fill_state      TEXT NOT NULL DEFAULT 'empty' CHECK (fill_state IN ('full','empty')),
    location        TEXT NOT NULL DEFAULT 'port' CHECK (location IN ('warehouse','port','in_transit','cuba','returned')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- dinero
CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY,
    vendor          TEXT NOT NULL,
    invoice_no      TEXT NOT NULL,
    invoice_date    TEXT,
    booking_no      TEXT,                      -- puede ser NULL si aún no se relaciona
    bl_no           TEXT,
    tank_no         TEXT,                      -- invoices por tanque (World Fuel, Port Consolidated, CCS)
    concept         TEXT,                      -- fuel | freight | demurrage | drayage | ...
    status          TEXT NOT NULL DEFAULT 'final' CHECK (status IN ('final','provisional')),
    iso_count       INTEGER,
    product         TEXT,
    gallons         REAL,
    price_per_gal   REAL,
    fuel_amount     REAL DEFAULT 0,
    freight_amount  REAL DEFAULT 0,
    other_amount    REAL DEFAULT 0,
    total           REAL NOT NULL,
    source_email_id TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (vendor, invoice_no)
);

CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY,
    pay_date        TEXT NOT NULL,
    vendor          TEXT NOT NULL,
    amount          REAL NOT NULL,
    wire_no         TEXT,
    booking_no      TEXT,
    concept         TEXT,   -- freight,fuel,drayage,brokerage,customs,return,documentation,insurance (separados por coma)
    iso_count       INTEGER,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Reparto que indicamos al proveedor al pagar ("$24,480.00 – Towards booking CAT40268984").
CREATE TABLE IF NOT EXISTS payment_splits (
    id          INTEGER PRIMARY KEY,
    payment_id  INTEGER NOT NULL REFERENCES payments(id),
    booking_no  TEXT,                          -- NULL = cargos generales (brokerage, customs...)
    amount      REAL NOT NULL,
    description TEXT
);

-- Lo pagado por booking (repartos + pagos hechos a un solo booking) vs lo facturado.
CREATE VIEW IF NOT EXISTS v_booking_money AS
SELECT vendor, booking_no, ROUND(SUM(paid),2) AS paid, ROUND(SUM(invoiced),2) AS invoiced,
       ROUND(SUM(paid) - SUM(invoiced),2) AS diff
FROM (
    SELECT p.vendor, s.booking_no, s.amount AS paid, 0 AS invoiced
      FROM payment_splits s JOIN payments p ON p.id = s.payment_id WHERE s.booking_no IS NOT NULL
    UNION ALL
    SELECT vendor, booking_no, amount, 0 FROM payments p
     WHERE booking_no IS NOT NULL AND NOT EXISTS (SELECT 1 FROM payment_splits s WHERE s.payment_id = p.id)
    UNION ALL
    SELECT vendor, booking_no, 0, total FROM invoices WHERE booking_no IS NOT NULL AND status = 'final'
) GROUP BY vendor, booking_no;

-- Qué parte de cada pago se aplica a cada invoice (N a N).
CREATE TABLE IF NOT EXISTS payment_allocations (
    id          INTEGER PRIMARY KEY,
    payment_id  INTEGER NOT NULL REFERENCES payments(id),
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id),
    amount      REAL NOT NULL,
    auto        INTEGER NOT NULL DEFAULT 0,        -- 1 = la hizo auto-apply (se puede recalcular)
    UNIQUE (payment_id, invoice_id)
);

-- ---------------------------------------------------------------- emails
CREATE TABLE IF NOT EXISTS emails (
    id          INTEGER PRIMARY KEY,
    message_id  TEXT NOT NULL UNIQUE,          -- evita procesar dos veces
    received_at TEXT,
    sender      TEXT,
    subject     TEXT,
    categories  TEXT,                          -- 'invoice,booking_change'
    extracted   TEXT,                          -- JSON con lo detectado
    processed   INTEGER NOT NULL DEFAULT 0,    -- 0 = pendiente de revisar
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Copia del texto de los emails relevantes: permite reprocesar (python cli.py reprocess)
-- cuando se mejora el lector, sin volver a descargar de Gmail.
CREATE TABLE IF NOT EXISTS email_cache (
    message_id  TEXT PRIMARY KEY,
    received_at TEXT,
    sender      TEXT,
    subject     TEXT,
    body        TEXT,
    recipients  TEXT
);

-- Cada comando manual (python cli.py pay/booking/tank/...) queda aquí; `rebuild` los vuelve a aplicar.
CREATE TABLE IF NOT EXISTS manual_log (
    id      INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    argv    TEXT NOT NULL                      -- JSON con los argumentos del comando
);

-- ---------------------------------------------------------------- historial
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    entity      TEXT NOT NULL,                 -- booking | tank | invoice | payment | ibc
    entity_key  TEXT NOT NULL,                 -- booking_no, tank_no, id...
    action      TEXT NOT NULL,                 -- created | updated | status_change | linked ...
    field       TEXT,
    old_value   TEXT,
    new_value   TEXT,
    source      TEXT                           -- manual | email:<message_id> | import
);
CREATE INDEX IF NOT EXISTS ix_events_entity ON events(entity, entity_key);

-- ---------------------------------------------------------------- vistas de dinero
-- Estado de cada invoice según lo aplicado.
CREATE VIEW IF NOT EXISTS v_invoice_status AS
SELECT i.*,
       COALESCE(SUM(a.amount),0)                    AS applied,
       ROUND(i.total - COALESCE(SUM(a.amount),0),2) AS balance,
       CASE WHEN COALESCE(SUM(a.amount),0) = 0 THEN 'pending'
            WHEN i.total - COALESCE(SUM(a.amount),0) > 0.009 THEN 'partial'
            ELSE 'paid' END                          AS pay_status
FROM invoices i LEFT JOIN payment_allocations a ON a.invoice_id = i.id
GROUP BY i.id;

-- Crédito disponible de cada pago = pago - invoices aplicadas.
CREATE VIEW IF NOT EXISTS v_payment_status AS
SELECT p.*,
       COALESCE(SUM(a.amount),0)                     AS applied,
       ROUND(p.amount - COALESCE(SUM(a.amount),0),2) AS credit,
       CASE WHEN COALESCE(SUM(a.amount),0) = 0 THEN 'unapplied'
            WHEN p.amount - COALESCE(SUM(a.amount),0) > 0.009 THEN 'with_credit'
            ELSE 'fully_applied' END                  AS pay_status
FROM payments p LEFT JOIN payment_allocations a ON a.payment_id = p.id
GROUP BY p.id;

-- Balance por proveedor: positivo = nos deben (crédito), negativo = debemos.
CREATE VIEW IF NOT EXISTS v_vendor_balance AS
SELECT vendor,
       ROUND(SUM(paid),2)     AS total_paid,
       ROUND(SUM(invoiced),2) AS total_invoiced,
       ROUND(SUM(paid) - SUM(invoiced),2) AS balance
FROM (
    SELECT vendor, amount AS paid, 0 AS invoiced FROM payments
    UNION ALL
    SELECT vendor, 0, total FROM invoices WHERE status = 'final'
) GROUP BY vendor;
