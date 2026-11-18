function Get-RestoredLedgerLink([object]$Source,[string]$Parent,[string]$ReceiptPin,[object]$Config) {
 Reference-AssertPrivateDirectory $Parent
 $folders=@(Get-ChildItem -LiteralPath $Parent -Directory -Filter 'cold-20261114-*');Inspect-Require ($folders.Count -le 32) 'LINK_SEARCH_LIMIT';$found=@()
 foreach($folder in $folders){
  Inspect-Require ($folder.Name -cmatch '^cold-20261114-[0-9a-f]{32}$') 'LINK_DIRECTORY_INVALID';Reference-AssertPrivateDirectory $folder.FullName
  $path=Join-Path $folder.FullName 'ISOLATED_RESTORE_RECEIPT.json';if(-not [IO.File]::Exists($path)){continue}
  $file=Trades-Open $path 1048576 $Source.streams
  if($file.sha256 -cne $ReceiptPin){continue}
  $r=Inspect-Json $file.body
  Assert-RestoreEvidence $r $Config
  $found+=,[pscustomobject]@{receipt_sha256=$file.sha256;account=$r.account}
 }
 Inspect-Require ($found.Count -eq 1) 'LINK_RESTORE_RECEIPT_NOT_UNIQUE'
 Trades-VerifySaved $Source
 return $found[0]
}
function New-LinkedRecoveryReview([object]$Preview,[object]$Restored) {
 Inspect-Require ($Preview.recovery_preview -ceq 'OFFLINE_EVIDENCE_LINKED_CONDITIONAL_PROPOSAL_KEEP_STOPPED') 'LINK_PREVIEW_INVALID'
 $a=$Restored.account
 Inspect-Require ($a.daily_key -ceq $Preview.stored_day13_report_day_kst -and $a.weekly_key -ceq $Preview.prior_snapshot.week_kst -and $a.orders -eq $Preview.preserve.orders -and $a.unresolved_requests -eq $Preview.preserve.unresolved_requests) 'LINK_BASELINE_REPORT_MISMATCH'
 $changes=@(
  [pscustomobject]@{field='daily_key';before=$a.daily_key;candidate=$Preview.candidate.day_kst;source='PINNED_RESTORED_SQL_REPORT_TO_SAVED_CANDIDATE'},
  [pscustomobject]@{field='weekly_key';before=$a.weekly_key;candidate=$Preview.candidate.week_kst;source='PINNED_RESTORED_SQL_REPORT_TO_SAVED_CANDIDATE'},
  [pscustomobject]@{field='daily_base';before=$Preview.prior_snapshot.daily_base_usdt;candidate=$Preview.candidate.daily_base_usdt;source='HISTORICAL_FINANCIAL_RECORDS_TO_SAVED_WALLET_CANDIDATE'},
  [pscustomobject]@{field='weekly_base';before=$Preview.prior_snapshot.weekly_base_usdt;candidate=$Preview.candidate.weekly_base_usdt;source='HISTORICAL_FINANCIAL_RECORDS_TO_SAVED_WALLET_CANDIDATE'}
 )
 return [pscustomobject]@{
  recovery_link='PINNED_RESTORED_LEDGER_REPORT_LINKED_CONDITIONAL_CHANGE_TABLE'
  restore_receipt_sha256=$Restored.receipt_sha256;restored_sql_state_fingerprint_sha256=$a.account_state_sha256
  backup_manifest_sha256=$Preview.evidence_hashes.backup_manifest_sha256
  restored_sql_report=[pscustomobject]@{daily_key=$a.daily_key;weekly_key=$a.weekly_key;paused=$a.paused;orders=$a.orders;unresolved_requests=$a.unresolved_requests;read_only=($a.transaction_read_only -ceq 'on')}
  conditional_changes=$changes;preserve=$Preview.preserve
  saved_observations_end_utc=$Preview.saved_observations_end_utc
  current_receipt_sha256=$Preview.evidence_hashes.current_receipt_sha256
  physical_source_comparison_context='USER_SCREENSHOT_REPORTED_TWO_PASSES_DB1355_RUNTIME30_MATCHED_PINNED_MANIFEST'
  physical_source_comparison_independently_rerun=$false
  current_source_sql_queried=$false;financial_fields_directly_read_from_restored_sql_report=$false
  financial_projection_source='PINNED_PRIOR_FINANCIAL_RECORDS_AND_LINKED_STORED_DAY13_ADOPTION_REPORT'
  full_prospective_ledger_diff_verified=$false
  account_state_hash_and_commit_hash_compared=$false
  account_state_hash_and_commit_hash_use_different_serializations=$true
  candidate_source='SAVED_WALLET_NOT_INDEPENDENTLY_RECONSTRUCTED_MIDNIGHT_EQUITY'
  blockers=@('NEW_APPLY_PATH_NOT_IMPLEMENTED','EXPLICIT_PERIOD_SOURCE_ACCEPTANCE_NOT_RECEIVED','FULL_CURRENT_LEDGER_FINANCIAL_DIFF_NOT_VERIFIED','HISTORICAL_REQUESTS_NOT_RESOLVED','LEGACY_APPLY_ANCHOR_EXCEEDS_7_DAYS','STARTUP_AND_RISK_GATES_NOT_COMPLETED')
  files_written=$false;credentials_accessed=$false;docker_used=$false;services_started=$false;actual_http_attempts=0
  apply_token_created=$false;baseline_adopted=$false;request_states_changed=$false;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false
 }
}
