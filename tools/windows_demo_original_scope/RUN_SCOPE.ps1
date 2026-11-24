[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$handles=@()
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256){throw 'SCOPE_TRANSFER_INVALID'}
 $manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 $required=@('__FILES__')
 if((@($manifest.files.PSObject.Properties.Name|Sort-Object)-join '|') -cne (@($required|Sort-Object)-join '|')){throw 'SCOPE_TRANSFER_INVALID'}
 foreach($p in $manifest.files.PSObject.Properties){if($p.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $p.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $p.Value){throw 'SCOPE_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 foreach($name in @('CONTEXT.ps1','COMMON.ps1','SUPPORT.ps1','SOURCE_SCOPE.ps1','COPY_CORE.ps1','COPY_EXT.ps1','COMPARE_ADMISSION.ps1','READ_CORE.ps1','BASELINE_CORE.ps1','PLAN_CORE.ps1','FINANCIAL_EXT.ps1')){. (Join-Path $PSScriptRoot $name)}
 $config=Read-RestoreJson (Join-Path $PSScriptRoot 'CONFIG.json')
 $receiptPath=Join-Path $PSScriptRoot 'SOURCE_ACCEPTANCE.json'
 if((Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $config.source_acceptance_file_sha256){throw 'SCOPE_ACCEPTANCE_MISMATCH'}
 $accepted=ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($receiptPath,[Text.Encoding]::UTF8))
 if($accepted.review_id -cne $config.accepted_review_id -or $accepted.proposal_sha256 -cne $config.accepted_proposal_sha256 -or $accepted.source_accepted -isnot [bool] -or -not $accepted.source_accepted -or $accepted.conditional_reuse_approved -isnot [bool] -or -not $accepted.conditional_reuse_approved){throw 'SCOPE_ACCEPTANCE_MISMATCH'}
 foreach($flag in @('original_database_write_authorized','original_database_start_authorized','original_owner_lease_takeover_authorized','existing_guard_bypass_authorized','request_resolution_authorized','resend_authorized','execution_approved','orders_enabled','live_trading_enabled')){if($accepted.$flag -isnot [bool] -or $accepted.$flag){throw 'SCOPE_ACCEPTANCE_AUTHORITY_INVALID'}}
 $backup=Find-RetainedBackup $config
 $receipt=Read-RestoreJson (Join-Path $backup 'BACKUP_RECEIPT.json')
 Assert-InputHashes $backup $config $receipt
 foreach($name in @('BACKUP_RECEIPT.json','BACKUP_STATE.json','manifest.json','database.tar','runtime.tar','images.tar')){$handles+=,(New-Object IO.FileStream((Join-Path $backup $name),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read))}
 Assert-InputHashes $backup $config $receipt
 $state=Read-RestoreJson (Join-Path $backup 'BACKUP_STATE.json')
 $original=Read-CopySources $config $receipt $state
 Assert-RestoreSourceSnapshot $original @() $false
 Assert-InputHashes $backup $config $receipt
 [pscustomobject]@{scope_check='ORIGINAL_SCOPE_AND_PINNED_BACKUP_CHECKED_NO_START';source_acceptance_recorded=$true;conditional_reuse_approved=$true;source_acceptance_receipt_sha256=$config.accepted_receipt_sha256;original_stop_scope_rechecked=$true;backup_input_hashes_rechecked=$true;source_bytes_compared=$false;current_original_sql_queried=$false;current_original_fence_verified=$false;fresh_account_evidence_checked=$false;conditional_reuse_evaluated=$false;original_owner_lease_policy_ready=$false;original_database_started=$false;original_database_changed=$false;original_database_write_authorized=$false;original_apply_ready=$false;containers_created=0;volumes_created=0;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
 Write-Output 'ORIGINAL_SCOPE_CHECK_COMPLETE_NO_START_NO_WRITES_NO_ORDERS'
 exit 0
} catch {
 $reason='SCOPE_CHECK_BLOCKED';if($_.Exception.Message -cmatch '^(SCOPE|RESTORE|COLD|HANDOFF|FINANCIAL|BASELINE|INSPECT|READ|REFERENCE|INCOME)_[A-Z0-9_]{1,100}$'){$reason=$_.Exception.Message}
 [pscustomobject]@{scope_check='BLOCKED_KEEP_STOPPED_NO_RETRY';failure_category=$reason;original_database_started=$false;original_database_changed=$false;original_apply_ready=$false;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
 exit 2
} finally {
 foreach($handle in $handles){$handle.Dispose()}
 if($null -ne (Get-Variable -Name CopyRestoreHandle -Scope Script -ErrorAction SilentlyContinue) -and $null -ne $script:CopyRestoreHandle){$script:CopyRestoreHandle.Dispose()}
}
