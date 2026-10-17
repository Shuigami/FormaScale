# Run in PowerShell as Administrator.
# Forwards Windows:8443 -> current WSL Ubuntu IP and opens the firewall.

$ErrorActionPreference = 'Stop'
$wslIp = (wsl -d Ubuntu-24.04 -- hostname -I).Trim().Split(' ')[0]
if (-not $wslIp) { throw 'WSL IP introuvable. Demarre Ubuntu-24.04 d''abord.' }

netsh interface portproxy delete v4tov4 listenport=8443 listenaddress=0.0.0.0 2>$null | Out-Null
netsh interface portproxy add v4tov4 listenport=8443 listenaddress=0.0.0.0 connectport=8443 connectaddress=$wslIp

Get-NetFirewallRule -DisplayName 'FormaScale Live 8443' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'FormaScale Live 8443' -Direction Inbound -Protocol TCP -LocalPort 8443 -Action Allow | Out-Null

Write-Host "Portproxy OK : 0.0.0.0:8443 -> ${wslIp}:8443"
Write-Host 'Pare-feu OK  : TCP 8443 autorise'
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
  ForEach-Object { Write-Host ("iPhone URL  : https://{0}:8443/phone" -f $_.IPAddress) }
