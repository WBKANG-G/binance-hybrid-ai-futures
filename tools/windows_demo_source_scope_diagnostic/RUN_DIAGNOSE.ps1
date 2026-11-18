param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256){throw 'TRANSFER_INVALID'}
 $m=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 $required=@('CONTEXT.ps1','SUPPORT.ps1','SOURCE_SCOPE.ps1','CONFIG.json','DIAGNOSTIC_CORE.ps1','RUN_DIAGNOSE.ps1','README_KO.md')
 if((@($m.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne (@($required|Sort-Object) -join '|')){throw 'TRANSFER_INVALID'}
 foreach($p in $m.files.PSObject.Properties){if($p.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $p.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $p.Value){throw 'TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 foreach($n in @('CONTEXT.ps1','SUPPORT.ps1','SOURCE_SCOPE.ps1','DIAGNOSTIC_CORE.ps1')){. (Join-Path $PSScriptRoot $n)}
 Invoke-ScopeDiagnostic $PSScriptRoot|ConvertTo-Json -Depth 6 -Compress|Write-Output
 Write-Output 'SOURCE_SCOPE_DIAGNOSTIC_COMPLETE_NO_CREATE_NO_START_NO_WRITES'
 exit 0
} catch {
 Write-Output '{"source_scope_diagnostic":"TRANSFER_OR_LAUNCHER_BLOCKED","helper_created":false,"originals_started":false,"source_data_written":false,"orders_enabled":false,"live_trading_enabled":false}'
 exit 2
}
