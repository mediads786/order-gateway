Set-Location D:\order-gateway
$files = @(
  'app\governance\approvals.py',
  'app\governance\registry.py',
  'app\adapters\mock.py',
  'app\adapters\odoo.py',
  'mock_erp\main.py'
)
foreach ($m in (Get-ChildItem 'migrations\versions' -Filter '0009*')) {
  $files += ('migrations\versions\' + $m.Name)
}
$out = New-Object System.Collections.ArrayList
foreach ($f in $files) {
  [void]$out.Add("===== FILE: $f =====")
  [void]$out.Add((Get-Content -Encoding utf8 -Raw $f))
}
[void]$out.Add("===== GIT DIFF: routes.py and proposers.py =====")
[void]$out.Add(((git diff -- app/governance/routes.py app/proposals/proposers.py) -join "`n"))
$text = $out -join "`n"
Set-Clipboard -Value $text
$text | Out-File -Encoding utf8 .\review-m9.txt
Write-Host ("Copied " + $text.Length + " characters to the clipboard and to review-m9.txt")
