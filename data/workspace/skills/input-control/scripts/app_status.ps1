param([Parameter(Mandatory=$true)][string]$Name)
# 用法: powershell -ExecutionPolicy Bypass -File app_status.ps1 -Name QQ
$procs = Get-Process | Where-Object {$_.ProcessName -like "$Name*"}
if (-not $procs) { Write-Output "no process matching '$Name'"; exit }
$before = @{}
$procs | ForEach-Object { $before[$_.Id] = $_.CPU }
Start-Sleep -Seconds 3
Write-Output "PID  Responding  CPU_total  CPU_3s  Mem_MB"
Get-Process | Where-Object {$_.ProcessName -like "$Name*"} | ForEach-Object {
    $d = $_.CPU - $before[$_.Id]
    "{0}  {1}  {2:N1}s  +{3:N2}s  {4}" -f $_.Id, $_.Responding, $_.CPU, $d, [int]($_.WorkingSet64/1MB)
}
