[CmdletBinding()]
param([Parameter(Mandatory)][string]$Wrapper)
$ErrorActionPreference = 'Stop'
$path = (Resolve-Path -LiteralPath $Wrapper).Path
if ([IO.Path]::GetFileName($path) -ne 'Invoke-RetryingTask.ps1') { throw '请选择 EliSR 游戏任务包装器' }
$source = [IO.File]::ReadAllText($path)
$stamp = '# Drive disc manager: optional successful early-slot step'
if ($source.Contains($stamp)) { return }
$anchor = "        if (" + '$classification.Kind' + " -eq 'Success') {"
$first = $source.IndexOf($anchor,[StringComparison]::Ordinal)
if ($first -lt 0 -or $source.LastIndexOf($anchor,[StringComparison]::Ordinal) -ne $first) { throw '宿主成功分支已变化' }
$newline = if ($source.Contains([string][char]13)) { [Environment]::NewLine } else { [string][char]10 }
$hook = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'early-hook.ps1')).Trim()
$lines = $hook -split '[\r\n]+'
$section = $anchor + $newline + '            ' + $stamp + $newline + (($lines | ForEach-Object { '            ' + $_ }) -join $newline)
$updated = $source.Replace($anchor,$section)
$parserErrors = $null; $tokens = $null
[void][System.Management.Automation.Language.Parser]::ParseInput($updated,[ref]$tokens,[ref]$parserErrors)
if ($parserErrors.Count) { throw $parserErrors[0].Message }
$backup = $path + '.pre-drive-disc-manager.' + [DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff')
$temp = $path + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
[IO.File]::WriteAllText($temp,$updated,[Text.UTF8Encoding]::new($true))
[IO.File]::Replace($temp,$path,$backup)
Write-Output $backup
