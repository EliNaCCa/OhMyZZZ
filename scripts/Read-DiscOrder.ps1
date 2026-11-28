param([switch]$Status, [switch]$BackgroundOnly, [string]$ResumeFrom)
$ErrorActionPreference = 'Stop'
$dragon = 'F:\OneDragon'
$base = Join-Path $dragon 'plugin_data\drive_disc_manager\1\live-reads'
$opener = Join-Path $env:USERPROFILE '.codex\skills\sr-eli-game\scripts\Open-ZZZ.ps1'
$active = @(Get-ScheduledTask -TaskName 'OhMyZZZ-Order-*' -ErrorAction SilentlyContinue | Where-Object State -eq 'Running')
if ($active.Count -gt 1) { throw '有多个读盘任务' }
if ($Status -or $active.Count) {
    $dir = if ($active.Count) { Join-Path $base $active[0].TaskName.Substring('OhMyZZZ-Order-'.Length) } else {
        (Get-ChildItem -LiteralPath $base -Directory -Filter 'order-*' | Sort-Object Name -Descending | Select-Object -First 1).FullName
    }
    if ($dir) {
        $file = if (Test-Path -LiteralPath "$dir/result.json") { 'result.json' } else { 'status.json' }
        if (Test-Path -LiteralPath "$dir/$file") { Get-Content -LiteralPath "$dir/$file" -Raw -Encoding utf8 }
    }
    return
}
& $opener -BackgroundOnly:$BackgroundOnly | Out-Host
$state = & $opener -Status | ConvertFrom-Json
if ($state.Width -ne 1920 -or $state.Height -ne 1080 -or $state.GamePid.Count -ne 1) { throw '远程游戏窗口未就绪' }
$worker = Join-Path $PSScriptRoot 'read_order_live.py'
$dir = Join-Path $base ('order-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$resume = ''
if ($ResumeFrom) {
    $previous = Get-Item -LiteralPath $ResumeFrom
    if (!$previous.PSIsContainer -or $previous.Parent.FullName -ne $base -or $previous.Name -notmatch '^order-[0-9]{8}-[0-9]{6}$') { throw '请选择本账号上次的扫描目录' }
    $resume = ' "' + $previous.FullName + '"'
}
New-Item -Path $dir -ItemType Directory | Out-Null
Copy-Item -LiteralPath $worker -Destination (Join-Path $dir 'worker.py')
$argsLine = '"' + $dir + '\worker.py" ' + $state.GamePid[0] + ' "' + $dir + '"' + $resume
$action = New-ScheduledTaskAction -Execute (Join-Path $dragon '.venv\Scripts\pythonw.exe') -Argument $argsLine -WorkingDirectory $dragon
$user = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$($state.User)" -LogonType Interactive -RunLevel Highest
$options = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$task = 'OhMyZZZ-Order-' + (Split-Path $dir -Leaf)
Register-ScheduledTask -TaskName $task -Action $action -Principal $user -Settings $options | Out-Null
Start-ScheduledTask -TaskName $task
Write-Output $dir
