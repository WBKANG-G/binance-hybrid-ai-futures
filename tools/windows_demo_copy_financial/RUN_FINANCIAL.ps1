param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$CheckOnly,[switch]$ExecuteRead)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$oldOutput=$OutputEncoding;$oldConsole=[Console]::OutputEncoding
$OutputEncoding=New-Object Text.UTF8Encoding($false);[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256){throw 'TRANSFER_INVALID'}
 $m=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 $required=@('CONTEXT.ps1','COMMON.ps1','SUPPORT.ps1','SOURCE_SCOPE.ps1','COPY_CORE.ps1','COPY_EXT.ps1','COMPARE_ADMISSION.ps1','FINANCIAL_EXT.ps1','READ_CORE.ps1','BASELINE_CORE.ps1','PLAN_CORE.ps1','RUN_FINANCIAL.ps1','restore_worker.py','cold_archive.py','account_check.sql','CONFIG.json','README_KO.md')
 if((@($m.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne (@($required|Sort-Object) -join '|')){throw 'TRANSFER_INVALID'}
 foreach($p in $m.files.PSObject.Properties){if($p.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $p.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $p.Value){throw 'TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 foreach($name in @('CONTEXT.ps1','COMMON.ps1','SUPPORT.ps1','SOURCE_SCOPE.ps1','COPY_CORE.ps1','COPY_EXT.ps1','COMPARE_ADMISSION.ps1','READ_CORE.ps1','BASELINE_CORE.ps1','PLAN_CORE.ps1','FINANCIAL_EXT.ps1')){. (Join-Path $PSScriptRoot $name)}
 $code=$null
 Invoke-IsolatedRestore $PSScriptRoot -CheckOnly:$CheckOnly -ExecuteRestore:$ExecuteRead|ForEach-Object {if($_ -is [int]){$code=$_}else{Write-Output $_}}
 if(@(0,2) -notcontains $code){throw 'LAUNCHER_INVALID'}
 exit $code
} catch {
 Write-Output '{"copy_financial_review":"BLOCKED_TRANSFER_OR_LAUNCHER","originals_started":false,"automatic_retry":false,"order_writes":0,"orders_enabled":false,"live_trading_enabled":false}'
 exit 2
} finally {$OutputEncoding=$oldOutput;[Console]::OutputEncoding=$oldConsole}
