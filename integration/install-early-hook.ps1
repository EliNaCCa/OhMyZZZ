[CmdletBinding()]
param([Parameter(Mandatory)][string]$Wrapper)
$ErrorActionPreference = 'Stop'
$path = (Resolve-Path -LiteralPath $Wrapper).Path
if ([IO.Path]::GetFileName($path) -ne 'Invoke-RetryingTask.ps1') { throw 'Select the EliSR game wrapper.' }
$original = [IO.File]::ReadAllText($path)
$lf = [string][char]10
$source = $original.Replace(([string][char]13 + $lf),$lf)
$success = '        if ($classification.Kind -eq ''Success'') {'
$firstCleanup = '            # Completion includes owned-resource cleanup; do not publish Completed'
$oldStamp = '            # Drive disc manager: optional successful early-slot step'
$stamp = '# OhMyZZZ early hook v2'
$extra = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'early-hook.ps1')).Replace([string][char]13,'').Trim()
function Replace-Once([string]$from,[string]$to) {
    $offset = $script:source.IndexOf($from,[StringComparison]::Ordinal)
    if ($offset -lt 0 -or $script:source.LastIndexOf($from,[StringComparison]::Ordinal) -ne $offset) { throw 'Wrapper layout changed. No files written.' }
    $script:source = $script:source.Remove($offset,$from.Length).Insert($offset,$to)
}
if ($source.Contains($stamp)) {
    if (-not $source.Contains($stamp + $lf + $extra) -or -not $source.Contains('    $farmProof = Get-OhMyZZZResume') -or
        -not $source.Contains('            try { Invoke-OhMyZZZAfterEarly }') -or -not $source.Contains('function Complete-OhMyZZZWrapperSuccess {')) {
        throw 'Installed early hook differs from this version.'
    }
    return
}
if ($source.Contains($oldStamp)) {
    $start = $source.IndexOf($oldStamp)
    $end = $source.IndexOf($firstCleanup,$start)
    if ($end -lt 0) { throw 'Unrecognized v1 hook.' }
    $old = $source.Substring($start,$end-$start)
    $body = (($old.Substring($oldStamp.Length).Trim([char]10) -split '\n') -replace '^            ','') -join $lf
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($body))).Replace('-','') } finally { $sha.Dispose() }
    if ($hash -ne '4473D0CE4D29F4F54E27CCCD7BFB4371163F4B4A6F913EF8A2A3B02C53C7BBC8') { throw 'Local v1 hook has edits. No files written.' }
    Replace-Once $old ''
}
# Reuse the host's verified completion sequence verbatim for normal success and recovery.
$begin = $source.IndexOf($success + $lf + $firstCleanup,[StringComparison]::Ordinal)
$last = '            exit 0' + $lf + '        }'
if ($begin -lt 0) { throw 'Expected completion branch not found.' }
$end = $source.IndexOf($last,$begin,[StringComparison]::Ordinal)
if ($end -lt 0) { throw 'Missing successful wrapper exit.' }
$tail = $source.Substring($begin+$success.Length+1,$end-($begin+$success.Length+1))
$mustContain = @('Complete-EliSROwnedBusiness -TaskConfig $config','Assert-EliSRCatchupDate -Date $CatchupDate',
    '$state.Status = ''Completed''','Save-State $state','Save-EliSRWrapperReceipt -Slot $script:ReceiptSlot -Status ''Completed''',
    'Append-History @{ Event = ''Completed''','Invoke-PostAction -TaskConfig $config -Context ''Success''')
$cursor = -1
foreach ($part in $mustContain) {
    $cursor = $tail.IndexOf($part,$cursor+1,[StringComparison]::Ordinal)
    if ($cursor -lt 0) { throw 'Completion contract changed.' }
}
$completed = '            Append-History @{ Event = ''Completed''; State = $state }'
if (-not $tail.Contains($completed)) { throw 'Wrapper completion log has changed.' }
$tailWithCleanup = $tail.Replace($completed,$completed+$lf+'            Clear-OhMyZZZCompletedCheckpoint')
$functions = $stamp + $lf + $extra + $lf + $lf + 'function Complete-OhMyZZZWrapperSuccess {' + $lf +
    (($tailWithCleanup -split '\n') -replace '^        ','' -join $lf) + '}' + $lf + $lf
$hookCall = @'
            try { Invoke-OhMyZZZAfterEarly } catch {
                Write-WrapperLog ('OhMyZZZ early maintenance deferred: ' + $_.Exception.Message) 'WARN'
            }
'@
Replace-Once ($success+$lf+$tail+$last) ($success+$lf+$hookCall.Replace([string][char]13,'')+$lf+$tailWithCleanup+$last)
Replace-Once 'function Get-TaskConfig {' ($functions+'function Get-TaskConfig {')
$resume = @'
    $farmProof = Get-OhMyZZZResume
    if ($null -ne $farmProof) {
        $state.Attempt = $farmProof.attempt
        $script:OhMyZZZProofWritten = $true
        Append-History @{Event='DriveDiscFarmRecovered';Run=$farmProof.id;Session=$CurrentSessionId}
        Write-WrapperLog 'Verified earlier farm success. Finishing interrupted early slot.'
        Complete-OhMyZZZWrapperSuccess
        exit 0
    }
'@
$sessionGuard = '    Assert-EliSRBusinessSession' + $lf + '    Write-WrapperLog ''Starting the installed application directly; diagnostics and repairs run only after failure.'''
Replace-Once $sessionGuard ($sessionGuard + $lf + $resume.Replace([string][char]13,''))
$errors = $null; $tokens = $null
[void][System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw $errors[0].Message }
if ($original.Contains([string][char]13)) { $source=$source.Replace($lf,([string][char]13+$lf)) }
$backup = $path + '.pre-drive-disc-manager.' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff')
$temp = $path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
try {
    [IO.File]::WriteAllText($temp,$source,[Text.UTF8Encoding]::new($true))
    if ([IO.File]::ReadAllText($path) -cne $original) { throw 'Wrapper changed during installation.' }
    [IO.File]::Replace($temp,$path,$backup)
    Write-Output $backup
} finally { if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) } }
