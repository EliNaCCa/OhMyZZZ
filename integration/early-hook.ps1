# Dot-source inside the existing successful OneDragonEarly branch, before owned game cleanup.
# All writes stay in the same session/mutex and the existing scheduler owns the timeout.
if ($Task -eq 'OneDragonZZZ' -and $script:ReceiptSlot -eq 'OneDragonEarly') {
    try {
        $managerEntry = Join-Path $config.WorkingDirectory 'plugins/drive_disc_manager/early_entry.py'
        $managerConfigs = Join-Path $config.WorkingDirectory 'plugin_data/drive_disc_manager/*/config.json'
        $managerEnabled = @(Get-ChildItem -Path $managerConfigs -File -ErrorAction SilentlyContinue | Where-Object {
            (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json).afterEarly -eq $true
        }).Count -gt 0
        if ($managerEnabled -and (Test-Path -LiteralPath $managerEntry)) {
            Assert-EliSRBusinessSession
            Update-ScheduleBoundary
            $managerSeconds = [int][Math]::Floor(($script:boundary.StopAt - (Get-Date)).TotalSeconds) - 90
            if ($managerSeconds -gt 0) {
                $managerRun = Invoke-ProcessAttempt -FilePath $config.FilePath -Arguments @($managerEntry) -WorkingDirectory $config.WorkingDirectory -TimeoutSeconds $managerSeconds
                Append-History @{Event='DriveDiscManagement';ExitCode=$managerRun.ExitCode;TimedOut=$managerRun.TimedOut}
            }
        }
    } catch {
        Write-WrapperLog ("驱动盘管理稍后重试：" + $_.Exception.Message) 'WARN'
    }
}
