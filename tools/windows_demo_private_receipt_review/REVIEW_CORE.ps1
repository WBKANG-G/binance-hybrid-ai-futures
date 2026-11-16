function Receipt-Require([bool]$Condition,[string]$Code) { if (-not $Condition) { throw $Code } }
function Receipt-Object([object]$Value) { return ($null -ne $Value -and $Value -is [pscustomobject]) }
function Receipt-Boolean([object]$Value,[bool]$Expected) { return ($Value -is [bool] -and $Value -eq $Expected) }
function Receipt-Integer([object]$Value,[long]$Expected) { return (($Value -is [int] -or $Value -is [long]) -and $Value -eq $Expected) }
function Receipt-Digest([byte[]]$Body) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($Body))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Receipt-Open([string]$Path) {
    $file=Get-Item -LiteralPath $Path
    Receipt-Require (-not $file.PSIsContainer -and ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'RECEIPT_FILE_INVALID'
    $handle=New-Object IO.FileStream($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        Receipt-Require ($handle.Length -gt 0 -and $handle.Length -le 1048576) 'RECEIPT_SIZE_LIMIT'
        $body=New-Object byte[] ([int]$handle.Length)
        $offset=0
        while ($offset -lt $body.Length) { $count=$handle.Read($body,$offset,$body.Length-$offset);Receipt-Require ($count -gt 0) 'RECEIPT_READ_FAILED';$offset+=$count }
        $encoding=New-Object Text.UTF8Encoding($false,$true)
        $json=$encoding.GetString($body) | ConvertFrom-Json
        Receipt-Require (Receipt-Object $json) 'RECEIPT_JSON_OBJECT_REQUIRED'
        return [pscustomobject]@{handle=$handle;body=$body;value=$json;sha256=(Receipt-Digest $body)}
    } catch { $handle.Dispose();throw }
}
function Receipt-CheckContent([object]$Value) {
    Receipt-Require (Receipt-Object $Value) 'RECEIPT_CONTENT_OBJECT_REQUIRED'
    Receipt-Require ($Value.account_content_review -ceq 'NARROW_FLAT_USDT_ARITHMETIC_CONSISTENT' -and (Receipt-Integer $Value.asset_rows_checked 1) -and (Receipt-Integer $Value.position_rows_checked 1) -and (Receipt-Boolean $Value.positive_usdt_wallet_observed $true)) 'RECEIPT_CONTENT_INVALID'
    foreach ($flag in @('account_configuration_verified','open_orders_verified','remote_account_identity_verified','baseline_amount_proposed','baseline_source_accepted','baseline_adoption_approved','execution_approved','orders_enabled','live_trading_enabled','proposal_input_shape_complete','caller_claims_present','operator_statement_independently_verified','referenced_proofs_verified','whole_history_coverage_verified','midnight_valuation_verified','stored_periods_verified_against_ledger')) { Receipt-Require (Receipt-Boolean $Value.$flag $false) 'RECEIPT_AUTHORITY_FLAG_INVALID' }
    $required=@('REMOTE_ACCOUNT_IDENTITY_AND_CREDENTIAL_CONTEXT_PROOF','ACCOUNT_AND_SYMBOL_CONFIGURATION_PROOF','REGULAR_AND_ALGO_OPEN_ORDER_PROOF','COMPLETE_TRADE_INCOME_TRANSFER_COVERAGE','DAY_BOUNDARY_VALUATION_OR_RECONSTRUCTION_PROOF','EXPLICIT_OPERATOR_STATEMENT_AND_SOURCE_ACCEPTANCE','HISTORICAL_REQUESTS_REQUIRE_INDEPENDENT_REMOTE_PROOF','CURRENT_PERIOD_AND_LOSS_HIGH_WATER_RECHECK')
    Receipt-Require ($Value.remaining_requirements -is [array] -and @($Value.remaining_requirements).Count -eq 8 -and (@($Value.remaining_requirements | Sort-Object) -join '|') -ceq (@($required | Sort-Object) -join '|')) 'RECEIPT_REQUIREMENTS_ARRAY_INVALID'
}
function Receipt-SameObject([object]$A,[object]$B) { return ((Receipt-Canonical $A) -ceq (Receipt-Canonical $B)) }
function Receipt-Canonical([object]$Value,[int]$Depth=0) {
    Receipt-Require ($Depth -le 24) 'RECEIPT_DEPTH_LIMIT'
    if ($null -eq $Value) {return 'null'}
    if ($Value -is [string] -or $Value -is [ValueType]) {return (ConvertTo-Json -InputObject $Value -Compress)}
    if ($Value -is [array]) {
        $parts=@();foreach ($entry in $Value) {$parts+=,(Receipt-Canonical $entry ($Depth+1))}
        return ('['+($parts -join ',')+']')
    }
    Receipt-Require (Receipt-Object $Value) 'RECEIPT_OBJECT_INVALID'
    $parts=@();foreach ($property in @($Value.PSObject.Properties|Sort-Object Name)) {
        $key=ConvertTo-Json -InputObject ([string]$property.Name) -Compress
        $parts+=,($key+':'+(Receipt-Canonical $property.Value ($Depth+1)))
    }
    return ('{'+($parts -join ',')+'}')
}
function Receipt-Numeric([object]$Value) {
    if ($Value -isnot [decimal] -and $Value -isnot [double] -and $Value -isnot [int] -and $Value -isnot [long]) {return $false}
    return (-not [double]::IsNaN([double]$Value) -and -not [double]::IsInfinity([double]$Value))
}
function Receipt-Flags([object]$Value,[string[]]$Names,[bool]$Expected) {
    Receipt-Require (Receipt-Object $Value) 'RECEIPT_OBJECT_INVALID'
    foreach ($name in $Names) {Receipt-Require (Receipt-Boolean $Value.$name $Expected) 'RECEIPT_FLAG_INVALID'}
}
function Review-PrivateReceipts([string]$Base,[object]$Config) {
    $opened=@()
    try {
        Receipt-Require ($Config.expected_directory_name -cmatch '^copy-private-week-v3-20261116-[0-9a-f]{32}$') 'RECEIPT_CONFIG_INVALID'
        $baseItem=Get-Item -LiteralPath $Base
        Receipt-Require ($baseItem.PSIsContainer -and ($baseItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'RECEIPT_BASE_INVALID'
        $folders=@(Get-ChildItem -LiteralPath $Base -Directory -Filter 'copy-private-week-v3-20261116-*')
        Receipt-Require ($folders.Count -le 32) 'RECEIPT_SEARCH_LIMIT'
        $selected=@($folders | Where-Object {$_.Name -ceq $Config.expected_directory_name})
        Receipt-Require ($selected.Count -eq 1 -and ($selected[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'RECEIPT_DIRECTORY_NOT_UNIQUE_OR_INVALID'
        $records=@{}
        foreach ($name in @('COPY_PRIVATE_WEEK_V3_RECEIPT.json','PRIVATE_WEEK_RESULT.json','PRIVATE_RESOURCES.json','RESTORE_RESOURCES.json','CLONE_CREATED.json')) {
            $record=Receipt-Open (Join-Path $selected[0].FullName $name);$opened+=,$record;$records[$name]=$record
        }
        $root=$records['COPY_PRIVATE_WEEK_V3_RECEIPT.json'].value
        Receipt-Require ($root.source_reference -ceq $Config.expected_source_reference -and $root.prior_evidence_receipt_sha256 -ceq $Config.prior_evidence_receipt_sha256 -and $root.prior_clock_receipt_sha256 -ceq $Config.prior_clock_receipt_sha256 -and $root.prior_copy_receipt_sha256 -ceq $Config.prior_copy_receipt_sha256 -and $root.backup_manifest_sha256 -ceq $Config.manifest_sha256 -and $root.backup_receipt_sha256 -ceq $Config.receipt_sha256 -and $root.isolated_restore -ceq 'COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED' -and $root.clone_network -ceq 'none') 'RECEIPT_LINEAGE_INVALID'
        Receipt-Flags $root @('copy_mounts_and_network_verified','prior_private_resources_stopped_verified','prior_private_v2_stopped_verified','input_hashes_reverified','archive_bytes_and_metadata_verified_before_clone_recovery','private_key_session_destroyed','clone_stopped','clone_clean_exit_verified') $true
        Receipt-Flags $root @('original_services_started','original_volume_mounted_in_restore_helpers','financial_write_attempted','full_financial_proof_verified','offline_handoff_performed','installation_ready','automatic_retry','execution_approved','orders_enabled','live_trading_enabled') $false
        Receipt-Require ((Receipt-Integer $root.actual_http_attempts 0) -and (Receipt-Integer $root.order_writes 0)) 'RECEIPT_COUNTER_INVALID'
        $account=$root.account
        Receipt-Require ((Receipt-Boolean $account.paused $true) -and (Receipt-Integer $account.orders 34) -and (Receipt-Integer $account.unresolved_requests 4) -and $account.transaction_read_only -ceq 'on' -and $account.daily_key -ceq '2026-11-13' -and $account.weekly_key -ceq '2026-11-09' -and $account.account_state_sha256 -cmatch '^[0-9a-f]{64}$') 'RECEIPT_ACCOUNT_INVALID'
        $clock=$root.private_week_result
        Receipt-Require (Receipt-Object $clock) 'RECEIPT_CLOCK_OBJECT_REQUIRED'
        Receipt-Require ((Receipt-SameObject $clock $records['PRIVATE_WEEK_RESULT.json'].value)) 'RECEIPT_COMPANION_MISMATCH'
        foreach ($spec in @(@('baseline_files_verified',89),@('candidate_files_verified',16),@('prior_audit_events',55),@('audit_events_after',56),@('prior_revision',58),@('revision_after',59),@('cumulative_weight_before',284),@('cumulative_weight_after',284),@('exchange_http_attempts',0),@('order_writes',0))) {Receipt-Require (Receipt-Integer $clock.($spec[0]) ([long]$spec[1])) 'RECEIPT_COUNTER_INVALID'}
        Receipt-Flags $clock @('clock_rebased','startup_hold_verified','prior_database_rows_preserved','prior_files_preserved','copy_read_sessions_read_only','copy_write_session_explicit','synthetic_key_file_unchanged') $true
        Receipt-Flags $clock @('financial_ledger_changed','period_baselines_changed','request_states_changed','automatic_retry','installation_ready','execution_approved','orders_enabled','live_trading_enabled') $false
        Receipt-Require ($clock.copy_clock_rehearsal -ceq 'CLOCK_REBASED_HOLD_VERIFIED_KEEP_STOPPED' -and (Receipt-Numeric $clock.startup_hold_seconds) -and $clock.startup_hold_seconds -ge 70.001 -and $clock.before_snapshot_sha256 -cmatch '^[0-9a-f]{64}$' -and $clock.after_snapshot_sha256 -cmatch '^[0-9a-f]{64}$' -and $clock.protected_database_sha256 -cmatch '^[0-9a-f]{64}$') 'RECEIPT_CLOCK_INVALID'
        $week=$clock.private_week_review
        Receipt-Require ((Receipt-Object $week) -and $week.private_week_copy_review -ceq 'SYNTHETIC_PRIVATE_COPY_VERIFIED_PERIODS_UNCHANGED' -and $week.review_day_kst -ceq '2026-11-16' -and $week.review_week_kst -ceq '2026-11-16' -and $week.stored_day_kst -ceq '2026-11-13' -and $week.stored_week_kst -ceq '2026-11-09') 'RECEIPT_WEEK_INVALID'
        Receipt-Flags $week @('day_and_week_evidence_still_required','week_boundary_acknowledged_for_observation_only') $true
        Receipt-Flags $week @('baseline_adoption_approved','baseline_source_accepted','daily_baseline_changed','weekly_baseline_changed','operator_attestation_received','remote_account_identity_verified','installation_ready','execution_approved','orders_enabled','live_trading_enabled') $false
        $pipeline=$clock.synthetic_evidence_rehearsal
        Receipt-Require ((Receipt-Object $pipeline) -and (Receipt-SameObject $pipeline $week.pipeline)) 'RECEIPT_PIPELINE_COMPANION_MISMATCH'
        Receipt-Require ($pipeline.synthetic_copy_evidence_rehearsal -ceq 'PIPELINE_VERIFIED_SYNTHETIC_ONLY_KEEP_STOPPED' -and $pipeline.before_snapshot_sha256 -ceq $clock.after_snapshot_sha256 -and $pipeline.after_snapshot_sha256 -cmatch '^[0-9a-f]{64}$' -and $pipeline.archive_receipt_sha256 -cmatch '^[0-9a-f]{64}$') 'RECEIPT_PIPELINE_INVALID'
        foreach ($spec in @(@('synthetic_sender_calls',1),@('audit_events_before',56),@('audit_events_after',58),@('exchange_http_attempts',0),@('order_writes',0))) {Receipt-Require (Receipt-Integer $pipeline.($spec[0]) ([long]$spec[1])) 'RECEIPT_COUNTER_INVALID'}
        Receipt-Flags $pipeline @('startup_ready_observed','evidence_binding_created','private_lease_opened_at_capture','private_tmpfs_credentials_used','protected_rows_and_files_preserved','shared_journal_changed','resource_checkpoint_changed') $true
        Receipt-Flags $pipeline @('copy_isolation_independently_verified','exchange_credentials_accessed','financial_ledger_changed','period_baselines_changed','request_states_changed','automatic_retry','installation_ready','execution_approved','orders_enabled','live_trading_enabled') $false
        Receipt-Require ((Receipt-Numeric $pipeline.private_preparation_seconds) -and $pipeline.private_preparation_seconds -ge 0 -and $pipeline.private_preparation_seconds -lt 300 -and [decimal]$pipeline.private_preparation_seconds -eq [decimal]$Config.expected_preparation_seconds) 'RECEIPT_PREPARATION_INVALID'
        Receipt-CheckContent $pipeline.content_review
        Receipt-Require ($pipeline.content_review.account_receipt_sha256 -ceq $pipeline.archive_receipt_sha256 -and $pipeline.content_review.response_body_sha256 -cmatch '^[0-9a-f]{64}$') 'RECEIPT_ARCHIVE_REFERENCE_INVALID'
        $diagnostic=$root.late_lease_result_diagnostic
        Receipt-Require ((Receipt-Object $diagnostic) -and $diagnostic.reason -ceq 'NONE' -and (Receipt-Boolean $diagnostic.late_lease_flag $true) -and $diagnostic.preparation_seconds_type -ceq 'System.Decimal' -and (Receipt-Numeric $diagnostic.preparation_seconds) -and [decimal]$diagnostic.preparation_seconds -eq [decimal]$pipeline.private_preparation_seconds -and (Receipt-Integer $diagnostic.preparation_limit_seconds 300) -and (Receipt-Integer $diagnostic.signing_lease_limit_seconds 75)) 'RECEIPT_LATE_DIAGNOSTIC_INVALID'
        $ids=@($root.retained_helper_ids);$private=@($root.private_helper_ids);$volumes=@($root.retained_clone_volumes)
        Receipt-Require ($ids.Count -eq 5 -and @($ids|Select-Object -Unique).Count -eq 5 -and $private.Count -eq 3 -and @($private|Select-Object -Unique).Count -eq 3 -and $root.clone_container_id -ceq $ids[1] -and $volumes.Count -eq 3 -and @($volumes|Select-Object -Unique).Count -eq 3) 'RECEIPT_RESOURCE_SET_INVALID'
        foreach ($id in $ids) {Receipt-Require ($id -is [string] -and $id -cmatch '^[0-9a-f]{64}$') 'RECEIPT_RESOURCE_ID_INVALID'}
        foreach ($id in $private) {Receipt-Require ($ids -ccontains $id -and $id -cne $ids[0] -and $id -cne $ids[1]) 'RECEIPT_RESOURCE_ID_INVALID'}
        Receipt-Require ($root.private_key_volume -cmatch '^demo-read-private-keys-[0-9a-f]{32}$') 'RECEIPT_RESOURCE_VOLUME_INVALID'
        for ($i=0;$i -lt 3;$i++) {$kind=@('database','runtime','socket')[$i];Receipt-Require ($volumes[$i] -ceq ('demo-copy-review-'+$kind+'-'+$Config.expected_directory_name.Substring($Config.expected_directory_name.Length-32))) 'RECEIPT_RESOURCE_VOLUME_INVALID'}
        $resources=$records['PRIVATE_RESOURCES.json'].value
        Receipt-Require ((Receipt-SameObject $resources.private_helper_ids $root.private_helper_ids) -and $resources.private_key_volume -ceq $root.private_key_volume -and (Receipt-Boolean $resources.synthetic_keys_only $true) -and (Receipt-Boolean $resources.private_key_session_destroyed $true) -and (Receipt-Boolean $resources.automatic_retry $false)) 'RECEIPT_PRIVATE_RESOURCE_MISMATCH'
        $restore=$records['RESTORE_RESOURCES.json'].value;$clone=$records['CLONE_CREATED.json'].value
        Receipt-Require ($restore.backup_receipt_sha256 -ceq $Config.receipt_sha256 -and $restore.manifest_sha256 -ceq $Config.manifest_sha256 -and (Receipt-Boolean $restore.automatic_resume $false) -and @($restore.owned_helper_ids).Count -eq 1 -and $restore.owned_helper_ids[0] -ceq $ids[0] -and (Receipt-SameObject $restore.clone_volumes $root.retained_clone_volumes) -and $clone.clone_container_id -ceq $root.clone_container_id -and (Receipt-SameObject $clone.clone_volumes $root.retained_clone_volumes) -and (Receipt-Boolean $clone.original_services_started $false)) 'RECEIPT_RESTORE_RESOURCE_MISMATCH'
        $scope=$clock.private_scope_policy
        Receipt-Require ((Receipt-Object $scope) -and $scope.private_read_scope -ceq 'OWNED_RO_KEY_AND_FRESH_COPY_MOUNTS_CHECKED') 'RECEIPT_SCOPE_INVALID'
        Receipt-Flags $scope @('registrar_has_only_key_volume','reader_key_mount_readonly','external_controller_freshness_attested') $true
        Receipt-Flags $scope @('copy_isolation_independently_verified','docker_socket_mounted','network_enabled','remote_account_identity_verified','execution_approved','orders_enabled','live_trading_enabled') $false
        $hashes=[ordered]@{}
        foreach ($name in $records.Keys) {
            $record=$records[$name];Receipt-Require ($record.handle.Length -eq $record.body.Length) 'RECEIPT_CHANGED_DURING_REVIEW';$record.handle.Position=0
            $body=New-Object byte[] ([int]$record.handle.Length);$offset=0
            while ($offset -lt $body.Length) {$count=$record.handle.Read($body,$offset,$body.Length-$offset);Receipt-Require ($count -gt 0) 'RECEIPT_READ_FAILED';$offset+=$count}
            Receipt-Require ($record.handle.Length -eq $record.body.Length -and (Receipt-Digest $body) -ceq $record.sha256) 'RECEIPT_CHANGED_DURING_REVIEW'
            $hashes[$name]=$record.sha256
        }
        return [pscustomobject]@{private_receipt_review='V3_STORED_RECEIPT_AND_FOUR_COMPANIONS_VERIFIED';receipt_sha256=$records['COPY_PRIVATE_WEEK_V3_RECEIPT.json'].sha256;file_sha256=[pscustomobject]$hashes;companion_records_checked=4;content_requirements_remaining=8;private_preparation_seconds=$pipeline.private_preparation_seconds;recorded_preparation_numeric_type=$diagnostic.preparation_seconds_type;private_lease_opened_at_capture=$true;signing_lease_limit_seconds=75;recorded_key_session_destroyed=$true;recorded_clone_stopped=$true;orders_preserved=34;unresolved_requests_preserved=4;stored_day_kst='2026-11-13';stored_week_kst='2026-11-09';reviewed_files_changed=$false;files_changed=$false;docker_used=$false;services_started=$false;current_docker_state_checked=$false;archive_response_bytes_replayed=$false;receipt_hash_compared_to_external_pin=$false;remote_account_identity_verified=$false;baseline_adoption_approved=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } finally {foreach ($record in $opened) {$record.handle.Dispose()}}
}
