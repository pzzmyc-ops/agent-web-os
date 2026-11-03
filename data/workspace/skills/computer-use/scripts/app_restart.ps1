param([Parameter(Mandatory=$true)][string]$Name)
# 用法: powershell -ExecutionPolicy Bypass -File app_restart.ps1 -Name QQ
# 杀进程并按原路径重启（English-only on purpose: 中文在 .ps1 里会因编码乱码解析失败）
$procs = Get-Process | Where-Object {$_.ProcessName -like "$Name*"}
if (-not $procs) { Write-Output "no process matching '$Name'"; exit }
$path = ($procs | Where-Object {$_.Path} | Select-Object -First 1).Path
Write-Output "killing $($procs.Count) process(es), exe=$path"
$procs | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
if ($path) {
    Start-Process $path
    Write-Output "restarted: $path"
} else {
    Write-Output "exe path not found; cannot auto-restart"
}
