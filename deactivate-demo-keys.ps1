Set-Location D:\order-gateway
$list = docker compose exec -T api python -m scripts.api_keys list 2>&1 | Out-String
Write-Host "---- keys before ----"
Write-Host $list
$names = [regex]::Matches($list, 'cancel-(operator|approver|admin)-[0-9a-f]{6}') | ForEach-Object { $_.Value } | Sort-Object -Unique
if (-not $names) { Write-Host "No cancel-* demo keys found."; return }
foreach ($n in $names) {
  Write-Host "Deactivating $n"
  docker compose exec -T api python -m scripts.api_keys deactivate --name $n 2>&1 | Out-String | Write-Host
}
Write-Host "---- keys after ----"
docker compose exec -T api python -m scripts.api_keys list 2>&1 | Out-String | Write-Host
