# Three-minute demo

This script uses the default mock ERP. Start with the Compose stack running and a fresh database. In another PowerShell window, run the setup block once. The admin pages need a configured `ADMIN_TOKEN` in `.env`.

## Setup

```powershell
$Api = "http://localhost:8002"
$Fault = "http://127.0.0.1:9001/admin/faults"
function Set-ErpFault($mode) {
  Invoke-RestMethod -Method Post -Uri $Fault -ContentType "application/json" -Body (@{ mode = $mode } | ConvertTo-Json)
}
function New-DemoOrder($ref) {
  $requestBody = @{
    source = "manual"; external_ref = $ref
    customer = @{ name = "Demo Customer"; email = "demo@example.com" }
    currency = "USD"
    lines = @(@{ sku = "ABC"; qty = 2; unit_price = "19.99" })
  } | ConvertTo-Json -Depth 5 -Compress
  $key = [guid]::NewGuid().ToString()
  $result = Invoke-RestMethod -Method Post -Uri "$Api/orders" -Headers @{ "Idempotency-Key" = $key } -ContentType "application/json" -Body $requestBody
  $result | Add-Member -NotePropertyName idempotency_key -NotePropertyValue $key -PassThru |
    Add-Member -NotePropertyName request_body -NotePropertyValue $requestBody -PassThru
}
```

## Timeline

| Time | What to type | What the audience sees | One-sentence explanation |
| --- | --- | --- | --- |
| 0:00 | Open `http://localhost:8002/admin` and log in with `ADMIN_TOKEN` | Orders list | Intake, worker, database and operations view are running together. |
| 0:20 | `$a = New-DemoOrder "DEMO-A"; $a` | Order reaches `CONFIRMED` | A valid order is stored and delivered by the worker. |
| 0:50 | `Set-ErpFault "error_500"; $b = New-DemoOrder "DEMO-B"` | `RETRYING`, attempts, next attempt | A temporary ERP failure is recorded and scheduled for retry. |
| 1:10 | `Start-Sleep -Seconds 2; Set-ErpFault "none"` | DEMO-B reaches `CONFIRMED` on its next attempt | Recovery does not require resubmitting the order. |
| 1:30 | `Set-ErpFault "error_500"; $c = New-DemoOrder "DEMO-C"; Start-Sleep -Seconds 25` | DEMO-C retries and then reaches `FAILED_DEAD` | The retry limit turns repeated temporary failure into a visible dead letter. |
| 2:15 | `Set-ErpFault "none"`, then click **Retry** on DEMO-C | `QUEUED`, `PROCESSING`, `CONFIRMED` | An operator can requeue a dead order while preserving its audit history. |
| 2:30 | Open DEMO-C detail | Received, queued, processing, retrying, failed, admin requeued, processing and confirmed events | The audit trail explains each state transition. |
| 2:45 | Replay `$a` with the same key and body (command below) | Same order ID; no duplicate | Idempotency returns the original order for the same key and request. |

The dead-letter interval depends on retry timing and jitter; with the default policy, allow about 30–45 seconds. The list and detail pages refresh every three seconds. Use the refresh toggle to pause or resume it.

Replay the exact first request:

```powershell
Invoke-RestMethod -Method Post -Uri "$Api/orders" -Headers @{ "Idempotency-Key" = $a.idempotency_key } -ContentType "application/json" -Body $a.request_body
```

## If something goes wrong

- Reset the fault without clearing ERP records: `Set-ErpFault "none"`.
- Reset all mock ERP memory: `Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:9001/admin/reset"`.
- Clear the whole demo and start again: `docker compose down -v`, then run the three quickstart commands in the README.
- Timing note: with the default retry policy (5 attempts, 2 s base backoff, jitter) an order reaches `FAILED_DEAD` in roughly 20 to 30 seconds. If it takes much longer, check that the ERP fault is set (`Set-ErpFault "error_500"`) and that the worker is running (`docker compose ps`).

## Optional Odoo demo (requires Module 4 setup)

Set `ERP_ADAPTER=odoo`, configure the Odoo URL and API key in `.env`, and use the existing signed shipment example in the README. Send a gateway order for a seeded SKU, show its confirmed sale order in Odoo, apply a one-unit signed shipment, and show the stock decrease. Resend that shipment and show that stock does not change again.
