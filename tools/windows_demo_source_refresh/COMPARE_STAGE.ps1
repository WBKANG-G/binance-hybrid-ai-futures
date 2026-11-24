 # Source admission still filters exactly the re-admitted stopped reader here.
 Assert-RestoreSourceSnapshot $original @() $false
 Assert-InputHashes $backup $config $receipt
 if($CheckOnly){Write-Output 'SOURCE_REFRESH_CHECK_PASSED_NO_HELPER_START';exit 0}
 $stage='ACQUIRE_EXCLUSIVE_COMPARE'
 $mutex=New-Object Threading.Mutex($false,'Global\WBKANG_DEMO_COLD_BACKUP')
 $locked=$mutex.WaitOne(0);if(-not $locked){throw 'COLD_ANOTHER_BACKUP_ACTIVE'}
 Assert-RestoreSourceSnapshot $original @() $false
 $stage='ADMIT_EXISTING_READ_ONLY_HELPER'
 Assert-RefreshReader $config $state $original
 $helper=$config.comparison_helper_id
 $directory=New-PrivateBackupDirectory ([Guid]::NewGuid().ToString('N'))
 Write-BackupJson (Join-Path $directory 'INTENT.json') @{source_reference=$config.source_reference;helper_id=$helper;helper_reused=$true;original_services_started=$false;automatic_retry=$false}
 # From this point the one owned reader is visible in container-set snapshots.
 $script:RefreshHelperVisible=$true
 Assert-RestoreSourceSnapshot $original @($helper) $false
 $stage='READ_ONLY_TWO_PASS_COMPARISON'
 Write-Output 'SOURCE_REFRESH_STAGE=READ_ONLY_TWO_PASS_COMPARISON'
 $worker=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'worker.py')))
 $library=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'READ_LIBRARY.py')))
 $manifest=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $backup 'manifest.json')))
 $payload="import base64,json`nREAD_LIBRARY=base64.b64decode('$library')`nMANIFEST=json.loads(base64.b64decode('$manifest'))`nexec(compile(base64.b64decode('$worker'),'<source-refresh>','exec'))"
 $value=Invoke-RefreshPayload $config $state $original $payload
 $stage='REVERIFY_STOPPED_READER_AND_SOURCES'
 Assert-RefreshReader $config $state $original
 $helperStopped=$true
 Assert-RestoreSourceSnapshot $original @($helper) $false
 Assert-InputHashes $backup $config $receipt
 $m=Read-RestoreJson (Join-Path $backup 'manifest.json') 16777216
 Assert-RefreshResult $value $m
 $result=[ordered]@{source_refresh='STOPPED_ORIGINAL_BYTES_AND_METADATA_MATCH_PINNED_BACKUP';source_reference=$config.source_reference;manifest_sha256=$config.manifest_sha256;backup_receipt_sha256=$config.receipt_sha256;passes=2;file_counts=$value.file_counts;logical_bytes=$value.logical_bytes;source_bytes_compared=$true;source_data_written=$false;comparison_helper_reused=$true;comparison_helper_id=$helper;helper_stopped_verified=$true;source_container_snapshots_rechecked=$true;source_acceptance_recorded=$true;conditional_reuse_approved=$true;restored_sql_reference_sha256=$script:FinancialPrior.account_state_sha256;current_original_sql_queried=$false;current_original_fence_verified=$false;original_services_started_by_tool=$false;original_database_write_authorized=$false;original_apply_ready=$false;containers_created=0;volumes_created=0;fresh_account_evidence_checked=$false;baseline_adopted=$false;request_states_changed=$false;execution_approved=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}
 Write-BackupJson (Join-Path $directory 'SOURCE_REFRESH_RECEIPT.json') $result
 $result['private_report_directory']=$directory
 $result['receipt_file_sha256']=Restore-Hash (Join-Path $directory 'SOURCE_REFRESH_RECEIPT.json')
 $result|ConvertTo-Json -Depth 6 -Compress|Write-Output
 Write-Output 'SOURCE_REFRESH_MATCHED_HELPER_STOPPED_NO_ORIGINAL_START_NO_ORDERS'
 exit 0
