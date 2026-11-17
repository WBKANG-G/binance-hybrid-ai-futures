param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$CheckOnly,[switch]$CreateDraft)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if ($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256) {throw 'INSPECT_TRANSFER_INVALID'}
 $manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 if ((@($manifest.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne 'PRIVATE_STORAGE.ps1|READ_CORE.ps1|README_KO.md|REQUEST_TEMPLATE_KO.txt|RUN_SUPPORT_DRAFT.ps1|SUPPORT_CORE.ps1') {throw 'INSPECT_TRANSFER_INVALID'}
 foreach ($entry in $manifest.files.PSObject.Properties) {if ($entry.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $entry.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value) {throw 'INSPECT_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 . (Join-Path $PSScriptRoot 'READ_CORE.ps1')
 . (Join-Path $PSScriptRoot 'PRIVATE_STORAGE.ps1')
 . (Join-Path $PSScriptRoot 'SUPPORT_CORE.ps1')
 $root=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
 $template=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'REQUEST_TEMPLATE_KO.txt'),[Text.Encoding]::UTF8)
 $result=Invoke-SupportDraft $root '2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d' $template -CheckOnly:$CheckOnly -CreateDraft:$CreateDraft
 $result|ConvertTo-Json -Depth 8 -Compress|Write-Output
 if ($result.support_draft -ceq 'PINNED_TWO_REQUEST_DRAFT_INPUTS_CHECKED_NO_FILE_CREATED') {Write-Output 'SUPPORT_DRAFT_CHECK_PASSED_NO_WRITES_NO_HTTP';exit 0}
 if ($result.support_draft -ceq 'PRIVATE_TWO_REQUEST_SUPPORT_DRAFT_CREATED_NOT_SENT') {Write-Output 'PRIVATE_SUPPORT_DRAFT_CREATED_NOT_SENT_NO_HTTP';exit 0}
 exit 2
} catch {
 $reason='INSPECT_LAUNCHER_FAILED';if ($_.Exception.Message -cmatch '^INSPECT_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
 [pscustomobject]@{support_draft='BLOCKED_NO_WRITES';messages_sent=0;failure_category=$reason;files_written=$false;docker_used=$false;services_started=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
 exit 2
}
