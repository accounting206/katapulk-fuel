# Pagos a proveedores que no llegan por email (confirmados por Katapulk el 30-Sep-2026).
# Cada comando queda guardado en la base (manual_log) y `python cli.py rebuild` los vuelve a aplicar.
# Correr una sola vez:  powershell -ExecutionPolicy Bypass -File pagos_2026-09.ps1
Set-Location $PSScriptRoot

# World Fuel / Ascent
python cli.py pay "World Fuel" 770226.00 pay_date=2026-08-17 concept=fuel "notes=Wire para varios bookings de gasolina"
python cli.py pay "World Fuel" 30000.00  pay_date=2026-08-24 concept=fuel "booking_no=CAT40226378 + CAT40226550" "notes=Fondos adicionales CAT40226378 / CAT40226550"
python cli.py pay "World Fuel" 200000.00 pay_date=2026-08-28 concept=fuel "notes=Advance partial payment para ordenes de combustible"
python cli.py pay "World Fuel" 35000.00  pay_date=2026-09-03 concept=fuel booking_no=CAT40249823 "notes=Fondos adicionales CAT40249823 - 7 ISO Diesel"
python cli.py pay "World Fuel" 13000.00  pay_date=2026-09-24 concept=fuel booking_no=CAT40262737 "notes=Fondos adicionales CAT40262737"

# Port Consolidated: prepago para CAT40262783 (5 ISO diesel) + CAT40262784 (1 ISO gasolina)
python cli.py pay "Port Consolidated" 219623.93 pay_date=2026-09-17 concept=fuel "booking_no=CAT40262783 + CAT40262784" "notes=Prepayment deposit CAT40262783 + CAT40262784"

# Triton Energy: pago completo
python cli.py pay "Triton Energy" 213418.88 pay_date=2026-09-17 concept=fuel "notes=Pago completo a Triton"

# Ushine Trucking: facturas viejas (no llegaron por email)
python cli.py pay "Ushine Trucking" 18500.00 pay_date=2026-08-17 concept=drayage "notes=FAC0017 + FAC0023 + FAC0024" invoices=FAC0017:1000,FAC0023:9000,FAC0024:8500

# Crowley inland drayage (6 tanques x $766.16) - comprobante enviado por Ariel el 21-Sep
python cli.py pay Crowley 4596.96 pay_date=2026-09-21 concept=drayage "notes=Crowley inland drayage 6 ISO x 766.16" splits=CAT40262783:3830.80,CAT40262784:766.16

python cli.py auto-apply
