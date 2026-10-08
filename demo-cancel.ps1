Set-Location D:\order-gateway
$Api = "http://localhost:8002"
$Mock = "http://localhost:9001"
$run = [guid]::NewGuid().ToString().Substring(0, 6)
$script:fail = 0

function Check($label, $ok) {
  if ($ok) { Write-Host "  OK   $label" } else { Write-Host "  FAIL $label"; $script:fail++ }
}

function New-DemoKey($role) {
  $name = "cancel-$role-$run"
  $output = docker compose exec -T api python -m scripts.api_keys create --name $name --role $role 2>&1 | Out-String
  $match = [regex]::Match($output, 'gw_[A-Za-z0-9_\-]+')
  if (-not $match.Success) { Write-Host $output; throw "Could not read the new $role key" }
  return $match.Value
}

function Call($Method, $Path, $Key, $Body, $Extra) {
  $headers = @{}
  if ($Key) { $headers["X-API-Key"] = $Key }
  if ($Extra) { foreach ($n in $Extra.Keys) { $headers[$n] = $Extra[$n] } }
  $code = 0
  $content = ""
  try {
    if ($null -ne $Body) {
      $r = Invoke-WebRequest -UseBasicParsing -Method $Method -Uri "$Api$Path" -Headers $headers -ContentType "application/json" -Body $Body
    } else {
      $r = Invoke-WebRequest -UseBasicParsing -Method $Method -Uri "$Api$Path" -Headers $headers
    }
    $code = [int]$r.StatusCode
    $content = $r.Content
  } catch [System.Net.WebException] {
    $response = $_.Exception.Response
    if ($null -eq $response) { throw }
    $code = [int]$response.StatusCode
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $content = $_.ErrorDetails.Message }
    if (-not $content) {
      try {
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $content = $reader.ReadToEnd()
        $reader.Dispose()
      } catch { }
    }
    $response.Dispose()
  }
  Write-Host "  HTTP $code $content"
  $json = $null
  try { $json = $content | ConvertFrom-Json } catch { }
  return [pscustomobject]@{ Code = $code; Json = $json }
}

function New-OrderViaGateway($Key, $Name) {
  $orderInput = @{
    source = "manual"
    customer = @{ name = $Name; email = "cancel-demo@example.com" }
    currency = "USD"
    lines = @(@{ sku = "ABC"; qty = 1; unit_price = "2.00" })
  }
  $body = @{ input = $orderInput } | ConvertTo-Json -Depth 6 -Compress
  $idem = [guid]::NewGuid().ToString()
  $r = Call "POST" "/workflows/create_order/requests" $Key $body @{ "Idempotency-Key" = $idem }
  return $r.Json.result.order_id
}

function Wait-OrderStatus($OrderId, $Wanted, $Seconds) {
  $order = $null
  for ($i = 0; $i -lt $Seconds; $i++) {
    $order = Invoke-RestMethod -UseBasicParsing -Uri "$Api/orders/$OrderId"
    if ($order.status -eq $Wanted) { return $order }
    Start-Sleep -Seconds 1
  }
  return $order
}

function Cancel-Body($OrderId, $Reason) {
  return (@{ input = @{ order_id = $OrderId; reason = $Reason } } | ConvertTo-Json -Depth 4 -Compress)
}

function Decision-Body($Decision, $Reason) {
  return (@{ decision = $Decision; reason = $Reason } | ConvertTo-Json -Compress)
}

Write-Host "== Setup: fresh keys (names cancel-*-$run), clean mock ERP faults"
$OperatorKey = New-DemoKey "operator"
$ApproverKey = New-DemoKey "approver"
$AdminKey = New-DemoKey "admin"
Invoke-RestMethod -UseBasicParsing -Method Post -Uri "$Mock/admin/faults" -ContentType "application/json" -Body '{"mode":"none","fail_rate":0,"latency_ms":0}' | Out-Null

try {
  Write-Host "== 1. ERP mode: order confirmed in the mock ERP"
  $orderA = New-OrderViaGateway $OperatorKey "Cancel Demo A"
  $a = Wait-OrderStatus $orderA "CONFIRMED" 30
  Check "order A reached CONFIRMED" ($a.status -eq "CONFIRMED")

  Write-Host "== 2. Request cancellation: nothing may change before approval"
  $req = Call "POST" "/workflows/cancel_order/requests" $OperatorKey (Cancel-Body $orderA "demo: customer changed mind") $null
  Check "operator request is 202 PENDING_APPROVAL" ($req.Code -eq 202 -and $req.Json.status -eq "PENDING_APPROVAL")
  $approvalA = $req.Json.approval_id
  $a = Invoke-RestMethod -UseBasicParsing -Uri "$Api/orders/$orderA"
  Check "order A still CONFIRMED after the request" ($a.status -eq "CONFIRMED")

  Write-Host "== 3. Duplicate request, wrong roles"
  $dup = Call "POST" "/workflows/cancel_order/requests" $OperatorKey (Cancel-Body $orderA "second try") $null
  Check "duplicate open cancellation is 409 cancel_already_pending" ($dup.Code -eq 409 -and $dup.Json.error -eq "cancel_already_pending")
  $opDecide = Call "POST" "/approvals/$approvalA/decision" $OperatorKey (Decision-Body "approve" "operator tries") $null
  Check "operator cannot decide (403 forbidden)" ($opDecide.Code -eq 403 -and $opDecide.Json.error -eq "forbidden")
  $apprReq = Call "POST" "/workflows/cancel_order/requests" $ApproverKey (Cancel-Body $orderA "approver tries") $null
  Check "approver cannot request (403)" ($apprReq.Code -eq 403)
  $unknown = Call "POST" "/workflows/cancel_order/requests" $OperatorKey (Cancel-Body ([guid]::NewGuid().ToString()) "no such order") $null
  Check "unknown order is 404 order_not_found" ($unknown.Code -eq 404 -and $unknown.Json.error -eq "order_not_found")

  Write-Host "== 4. Approver approves: ERP cancel executes"
  $ok = Call "POST" "/approvals/$approvalA/decision" $ApproverKey (Decision-Body "approve" "demo approve") $null
  Check "approval EXECUTED in erp mode" ($ok.Code -eq 200 -and $ok.Json.status -eq "EXECUTED" -and $ok.Json.result.mode -eq "erp")
  $a = Invoke-RestMethod -UseBasicParsing -Uri "$Api/orders/$orderA"
  Check "order A is CANCELLED" ($a.status -eq "CANCELLED")
  $again = Call "POST" "/workflows/cancel_order/requests" $OperatorKey (Cancel-Body $orderA "again") $null
  Check "cancelling a cancelled order is 409 order_not_cancellable" ($again.Code -eq 409 -and $again.Json.error -eq "order_not_cancellable")

  Write-Host "== 5. Local mode with the worker stopped: self-approval, reject, approve"
  docker compose stop worker 2>&1 | Out-Null
  $orderB = New-OrderViaGateway $OperatorKey "Cancel Demo B"
  $b = Invoke-RestMethod -UseBasicParsing -Uri "$Api/orders/$orderB"
  Write-Host ("  order B status: " + $b.status)
  Check "order B is waiting (RECEIVED or QUEUED, worker stopped)" ($b.status -eq "QUEUED" -or $b.status -eq "RECEIVED")
  $adminReq = Call "POST" "/workflows/cancel_order/requests" $AdminKey (Cancel-Body $orderB "admin request") $null
  Check "admin request is 202" ($adminReq.Code -eq 202)
  $approvalB1 = $adminReq.Json.approval_id
  $self = Call "POST" "/approvals/$approvalB1/decision" $AdminKey (Decision-Body "approve" "admin approves self") $null
  Check "self-approval is 403 self_approval_not_allowed" ($self.Code -eq 403 -and $self.Json.error -eq "self_approval_not_allowed")
  $rej = Call "POST" "/approvals/$approvalB1/decision" $ApproverKey (Decision-Body "reject" "demo reject") $null
  Check "approver reject is 200 REJECTED" ($rej.Code -eq 200 -and $rej.Json.status -eq "REJECTED")
  $b = Invoke-RestMethod -UseBasicParsing -Uri "$Api/orders/$orderB"
  Write-Host ("  order B status after reject: " + $b.status)
  Check "order B unchanged (still waiting) after reject" ($b.status -eq "QUEUED" -or $b.status -eq "RECEIVED")
  $req2 = Call "POST" "/workflows/cancel_order/requests" $OperatorKey (Cancel-Body $orderB "second request") $null
  Check "new request after a reject is 202" ($req2.Code -eq 202)
  $approvalB2 = $req2.Json.approval_id
  $ok2 = Call "POST" "/approvals/$approvalB2/decision" $ApproverKey (Decision-Body "approve" "demo approve local") $null
  Check "approval EXECUTED in local mode" ($ok2.Code -eq 200 -and $ok2.Json.status -eq "EXECUTED" -and $ok2.Json.result.mode -eq "local")
  $b = Invoke-RestMethod -UseBasicParsing -Uri "$Api/orders/$orderB"
  Check "order B is CANCELLED" ($b.status -eq "CANCELLED")
}
finally {
  docker compose start worker 2>&1 | Out-Null
}

Write-Host "== 6. Worker restarted: it must not pick up the cancelled order"
Start-Sleep -Seconds 6
$b = Invoke-RestMethod -UseBasicParsing -Uri "$Api/orders/$orderB"
Check "order B still CANCELLED after worker restart" ($b.status -eq "CANCELLED")

Write-Host "== 7. Unknown ERP state: refuse, never guess"
try {
  Invoke-RestMethod -UseBasicParsing -Method Post -Uri "$Mock/admin/faults" -ContentType "application/json" -Body '{"mode":"error_500","fail_rate":0,"latency_ms":0}' | Out-Null
  $orderD = New-OrderViaGateway $OperatorKey "Cancel Demo D"
  $d = Wait-OrderStatus $orderD "RETRYING" 25
  Check "order D is RETRYING (ERP failing)" ($d.status -eq "RETRYING")
  $unk = Call "POST" "/workflows/cancel_order/requests" $OperatorKey (Cancel-Body $orderD "demo unknown state") $null
  Check "cancel refused with 409 (erp_state_unknown or order_in_progress)" ($unk.Code -eq 409 -and ($unk.Json.error -eq "erp_state_unknown" -or $unk.Json.error -eq "order_in_progress"))
}
finally {
  Invoke-RestMethod -UseBasicParsing -Method Post -Uri "$Mock/admin/faults" -ContentType "application/json" -Body '{"mode":"none","fail_rate":0,"latency_ms":0}' | Out-Null
}

Write-Host ""
if ($script:fail -eq 0) {
  Write-Host "ALL CHECKS MATCHED"
} else {
  Write-Host ("" + $script:fail + " CHECK(S) FAILED - paste this whole output")
}
Write-Host "Demo keys created: cancel-operator-$run, cancel-approver-$run, cancel-admin-$run (deactivate them afterwards)"
