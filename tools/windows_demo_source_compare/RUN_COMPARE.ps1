param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$CheckOnly,[switch]$CompareOnce)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$oldOutput=$OutputEncoding;$oldConsole=[Console]::OutputEncoding
$OutputEncoding=New-Object Text.UTF8Encoding($false);[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256){throw 'COMPARE_TRANSFER_INVALID'}
 $m=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 $required=@('RUN_COMPARE.ps1','COMPARE_CORE.ps1','worker.py','README_KO.md','CONTEXT.ps1','SUPPORT.ps1','SOURCE_SCOPE.ps1','READ_LIBRARY.py','CONFIG.json')
 if((@($m.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne (@($required|Sort-Object) -join '|')){throw 'COMPARE_TRANSFER_INVALID'}
 foreach($p in $m.files.PSObject.Properties){if($p.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $p.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $p.Value){throw 'COMPARE_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 foreach($name in @('CONTEXT.ps1','SUPPORT.ps1','SOURCE_SCOPE.ps1','COMPARE_CORE.ps1')){. (Join-Path $PSScriptRoot $name)}
 $code=$null
 Invoke-SourceCompare $PSScriptRoot -CheckOnly:$CheckOnly -CompareOnce:$CompareOnce|ForEach-Object {if($_ -is [int]){$code=$_}else{Write-Output $_}}
 if(@(0,2) -notcontains $code){throw 'COMPARE_LAUNCHER_FAILED'}
 exit $code
} catch {
 [pscustomobject]@{source_compare='BLOCKED_TRANSFER_OR_LAUNCHER';failure_category='COMPARE_TRANSFER_OR_LAUNCHER_FAILED';originals_started=$false;source_data_written=$false;automatic_retry=$false;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output;exit 2
} finally {$OutputEncoding=$oldOutput;[Console]::OutputEncoding=$oldConsole}
