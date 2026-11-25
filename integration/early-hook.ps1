# Installed inline into the scheduler wrapper. Its application-use lock is already held.
function Get-OhMyZZZCheckpointPath {
    Join-Path $StateDir 'ohmyzzz-OneDragonEarly-farm.json'
}

function Get-OhMyZZZResume {
    if ($Task -ne 'OneDragonZZZ' -or $script:ReceiptSlot -ne 'OneDragonEarly') { return }
    $path = Get-OhMyZZZCheckpointPath
    if (-not [IO.File]::Exists($path)) { return }
    $proof = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $now = [DateTimeOffset]::Now.ToOffset([TimeSpan]::FromHours(8))
    if ($proof.date -match '^\d{4}-\d{2}-\d{2}$' -and $proof.date -lt $now.ToString('yyyy-MM-dd')) { return }
    $finished = [DateTimeOffset]::FromUnixTimeMilliseconds($proof.finished).ToOffset([TimeSpan]::FromHours(8))
    if ($proof.date -ne $now.ToString('yyyy-MM-dd') -or $finished.ToString('yyyy-MM-dd') -ne $proof.date -or
        $finished -gt $now -or $proof.task -ne $Task -or $proof.slot -ne $script:ReceiptSlot -or
        $proof.id -notmatch '^[0-9a-f]{32}$' -or $proof.kind -ne 'Success' -or $proof.session -le 0 -or
        $proof.sid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -or $proof.homePath -ne $config.WorkingDirectory) {
        throw 'OhMyZZZ early farm receipt cannot be verified.'
    }
    # Independent wrapper history corroborates the checkpoint. A plugin receipt cannot complete a farm.
    $event = Get-Content -LiteralPath $HistoryPath -Encoding UTF8 | Select-String -SimpleMatch $proof.id | ForEach-Object {
        $item = $_.Line | ConvertFrom-Json
        if ($item.Event -eq 'DriveDiscFarmSucceeded' -and $item.Run -eq $proof.id -and $item.Session -eq $proof.session -and
            $item.Attempt -eq $proof.attempt -and $item.Finished -eq $proof.finished) { $item }
    }
    if (@($event).Count -ne 1 -or $PostAction -eq 'Hibernate') { throw 'Early farm evidence or recovery post-action is invalid.' }
    # Initialization has already rejected a preexisting game in this session. Also check a prior
    # session and a surviving Python child before allowing the standard completion branch.
    $survivors = @(Get-Process -Name $config.CleanupProcesses -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $proof.session })
    $entry = Join-Path $config.WorkingDirectory 'plugins\drive_disc_manager\early_entry.py'
    $children = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop | Where-Object {
        $_.SessionId -eq $proof.session -and (-not $_.CommandLine -or $_.CommandLine.Replace('/','\').IndexOf($entry,[StringComparison]::OrdinalIgnoreCase) -ge 0)
    })
    if ($survivors.Count -or $children.Count) { throw 'Previous early game or disc manager is still open. Retry after it exits.' }
    return $proof
}

function Clear-OhMyZZZCompletedCheckpoint {
    if ($Task -eq 'OneDragonZZZ' -and $script:ReceiptSlot -eq 'OneDragonEarly' -and $script:OhMyZZZProofWritten) {
        try { [IO.File]::Delete((Get-OhMyZZZCheckpointPath)) }
        catch { Write-WrapperLog ('OhMyZZZ checkpoint cleanup: '+$_.Exception.Message) 'WARN' }
    }
}

function Invoke-OhMyZZZAfterEarly {
    if ($Task -ne 'OneDragonZZZ' -or $script:ReceiptSlot -ne 'OneDragonEarly') { return }
    $managerEntry = Join-Path $config.WorkingDirectory 'plugins\drive_disc_manager\early_entry.py'
    $managerConfigs = Join-Path $config.WorkingDirectory 'plugin_data\drive_disc_manager\*\config.json'
    $enabled = @(Get-ChildItem -Path $managerConfigs -File -ErrorAction SilentlyContinue | Where-Object {
        $_.Directory.Name -match '^[1-9][0-9]*$' -and (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json).afterEarly -eq $true
    })
    if (-not $enabled.Count -or -not (Test-Path -LiteralPath $managerEntry)) { return }
    Assert-EliSRBusinessSession
    Update-ScheduleBoundary
    $seconds = [int][Math]::Floor(($script:boundary.StopAt - (Get-Date)).TotalSeconds) - 90
    if ($seconds -le 0) { Append-History @{Event='DriveDiscManagement';Status='SkippedDeadline'}; return }
    Update-EliSROwnedBusiness
    $game = @(Get-EliSRLiveCleanupProcesses @('ZenlessZoneZero') | Where-Object { Test-EliSROwnedIdentity $_ })
    if ($game.Count -ne 1) { Append-History @{Event='DriveDiscManagement';Status='SkippedGameClosed'}; return }
    $run = [Guid]::NewGuid().ToString('N')
    $finished = [DateTimeOffset]::Now.ToOffset([TimeSpan]::FromHours(8))
    if ($finished.Date -ne ([DateTimeOffset]$script:AttemptStartedAt).ToOffset([TimeSpan]::FromHours(8)).Date) {
        Append-History @{Event='DriveDiscManagement';Status='SkippedDayBoundary'}; return
    }
    $proof = @{id=$run;kind=$classification.Kind;task=$Task;slot=$script:ReceiptSlot;date=$finished.ToString('yyyy-MM-dd');
        finished=$finished.ToUnixTimeMilliseconds();attempt=$attempt;session=$CurrentSessionId;homePath=$config.WorkingDirectory;
        sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value}
    Append-History @{Event='DriveDiscFarmSucceeded';Run=$run;Attempt=$attempt;Finished=$proof.finished;Session=$CurrentSessionId}
    Write-EliSRPowerEvidence -Name ([IO.Path]::GetFileName((Get-OhMyZZZCheckpointPath))) -Value $proof
    $script:OhMyZZZProofWritten = $true
    $arguments = @($managerEntry,'--run',$run,'--date',$proof.date,'--game-pid',[string]$game[0].Id,
        '--game-ticks',[string]$game[0].StartTime.ToUniversalTime().Ticks,'--session',[string]$CurrentSessionId)
    $child = Invoke-ProcessAttempt -FilePath $config.FilePath -Arguments $arguments -WorkingDirectory $config.WorkingDirectory -TimeoutSeconds $seconds
    Append-History @{Event='DriveDiscManagement';Run=$run;ExitCode=$child.ExitCode;TimedOut=$child.TimedOut}
}
