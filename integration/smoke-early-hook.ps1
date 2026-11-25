# Temporary wrapper state and a simulated manager child. No game or scheduled task is started.
param([Parameter(Mandatory)][string]$Wrapper)
$ErrorActionPreference = 'Stop'
$dir = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ('ohmyzzz-smoke-'+[guid]::NewGuid().ToString('N')))
$copy = Join-Path $dir 'Invoke-RetryingTask.ps1'
Copy-Item -LiteralPath $Wrapper -Destination $copy
$installer = Join-Path $PSScriptRoot 'install-early-hook.ps1'
$null = & $installer $copy
$hash = (Get-FileHash $copy).Hash
$null = & $installer $copy
if ((Get-FileHash $copy).Hash -ne $hash) { throw 'Installer was not idempotent' }
$installed = [IO.File]::ReadAllText($copy)
# Extract only the generated dispatch, never execute the real farm, session setup, power or cleanup code.
$resumeOffset = $installed.IndexOf('    $farmProof = Get-OhMyZZZResume')
$resumeEnd = $installed.IndexOf('        exit 0',$resumeOffset)
$guard = [ScriptBlock]::Create($installed.Substring($resumeOffset,$resumeEnd-$resumeOffset)+'        return'+[Environment]::NewLine+'    }'+[Environment]::NewLine+'    $script:farms++')
. (Join-Path $PSScriptRoot 'early-hook.ps1')
$Task = 'OneDragonZZZ'; $script:ReceiptSlot = 'OneDragonEarly'; $StateDir = $dir.FullName
$HistoryPath = Join-Path $StateDir 'history.jsonl'; $PostAction='None'; $CurrentSessionId=17
$config = @{WorkingDirectory=$dir.FullName;CleanupProcesses=@('ZenlessZoneZero');FilePath='fixture'}
$managerEntry = Join-Path $dir 'plugins\drive_disc_manager\early_entry.py'
$pluginConfig = Join-Path $dir 'plugin_data\drive_disc_manager\1\config.json'
foreach ($item in @($managerEntry,$pluginConfig)) { [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($item)) }
[IO.File]::WriteAllText($managerEntry,'fixture'); [IO.File]::WriteAllText($pluginConfig,'{"afterEarly":true}')
$script:farms=0; $script:children=0; $script:completions=0; $state=@{}; $attempt=1
$script:AttemptStartedAt=Get-Date; $script:boundary=@{StopAt=(Get-Date).AddMinutes(4)}
$classification=@{Kind='Success'}; $script:alive=$false
function Assert-EliSRBusinessSession {}
function Update-ScheduleBoundary {}
function Update-EliSROwnedBusiness {}
function Get-EliSRLiveCleanupProcesses { [pscustomobject]@{Id=42;StartTime=(Get-Date)} }
function Test-EliSROwnedIdentity { $true }
function Get-Process { if ($script:alive) { [pscustomobject]@{SessionId=17} } }
function Get-CimInstance {}
function Append-History($entry) { ($entry | ConvertTo-Json -Compress) | Add-Content -LiteralPath $HistoryPath -Encoding UTF8 }
function Write-EliSRPowerEvidence($Name,$Value) { ($Value|ConvertTo-Json) | Set-Content -LiteralPath (Join-Path $StateDir $Name) -Encoding UTF8 }
function Write-WrapperLog {}
function Complete-OhMyZZZWrapperSuccess { $script:completions++; Clear-OhMyZZZCompletedCheckpoint }
function Invoke-ProcessAttempt($FilePath,$Arguments,$WorkingDirectory,$TimeoutSeconds) {
    if ($Arguments[0] -ne $managerEntry -or $Arguments -notcontains '--run' -or $TimeoutSeconds -gt 150) {throw 'Unexpected launch'}
    $script:children++
    throw 'fixture interruption'
}
& $guard
try { Invoke-OhMyZZZAfterEarly; throw 'Missing interruption' } catch { if ($_.Exception.Message -ne 'fixture interruption') {throw} }
$script:OhMyZZZProofWritten=$false
$script:alive=$true
try { & $guard; throw 'Live process accepted' } catch { if ($_.Exception.Message -notlike 'Previous early game*') {throw} }
$script:alive=$false
& $guard
if ($script:farms -ne 1 -or $script:children -ne 1 -or $script:completions -ne 1 -or (Test-Path (Get-OhMyZZZCheckpointPath))) { throw 'Incorrect resume or checkpoint cleanup' }
Write-Output 'PASS early interruption: one farm, one child, live game held, one recovery completion.'
