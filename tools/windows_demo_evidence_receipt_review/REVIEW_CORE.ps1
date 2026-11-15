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
function Review-EvidenceReceipts([string]$Base,[object]$Config) {
    $handles=@();$receiptCandidates=@();$all=@()
    try {
        $baseItem=Get-Item -LiteralPath $Base
        Receipt-Require ($baseItem.PSIsContainer -and ($baseItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'RECEIPT_BASE_INVALID'
        $folders=@(Get-ChildItem -LiteralPath $Base -Directory -Filter 'copy-evidence-20261115-*')
        Receipt-Require ($folders.Count -le 32) 'RECEIPT_SEARCH_LIMIT'
        foreach ($folder in $folders) {
            Receipt-Require (($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -and $folder.Name -cmatch '^copy-evidence-20261115-[0-9a-f]{32}$') 'RECEIPT_DIRECTORY_INVALID'
            $path=Join-Path $folder.FullName 'COPY_EVIDENCE_RECEIPT.json'
            if ([IO.File]::Exists($path)) {
                $item=Receipt-Open $path;$handles+=,$item.handle;$all+=,$item
                if ($item.value.source_reference -ceq $Config.expected_source_reference) { $receiptCandidates+=,[pscustomobject]@{directory=$folder.FullName;receipt=$item} }
            }
        }
        Receipt-Require ($receiptCandidates.Count -eq 1) 'RECEIPT_NOT_UNIQUE'
        $selected=$receiptCandidates[0];$receipt=$selected.receipt.value
        Receipt-Require ($receipt.prior_clock_receipt_sha256 -ceq $Config.prior_clock_receipt_sha256 -and $receipt.prior_copy_receipt_sha256 -ceq $Config.prior_copy_receipt_sha256 -and $receipt.backup_manifest_sha256 -ceq $Config.manifest_sha256 -and $receipt.backup_receipt_sha256 -ceq $Config.receipt_sha256 -and $receipt.isolated_restore -ceq 'COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED' -and $receipt.clone_network -ceq 'none') 'RECEIPT_LINEAGE_INVALID'
        foreach ($flag in @('copy_mounts_and_network_verified','input_hashes_reverified','clone_stopped','clone_clean_exit_verified','archive_bytes_and_metadata_verified_before_clone_recovery')) { Receipt-Require (Receipt-Boolean $receipt.$flag $true) 'RECEIPT_COMPLETION_FLAG_INVALID' }
        foreach ($flag in @('original_services_started','original_volume_mounted_in_restore_helpers','financial_write_attempted','installation_ready','automatic_retry','execution_approved','orders_enabled','live_trading_enabled')) { Receipt-Require (Receipt-Boolean $receipt.$flag $false) 'RECEIPT_AUTHORITY_FLAG_INVALID' }
        $clock=$receipt.copy_evidence_result;Receipt-Require (Receipt-Object $clock) 'RECEIPT_CLOCK_OBJECT_REQUIRED'
        $pipeline=$clock.synthetic_evidence_rehearsal;Receipt-Require (Receipt-Object $pipeline) 'RECEIPT_PIPELINE_OBJECT_REQUIRED'
        Receipt-CheckContent $pipeline.content_review
        foreach ($spec in @(@($clock,'prior_audit_events',55),@($clock,'audit_events_after',56),@($pipeline,'audit_events_before',56),@($pipeline,'audit_events_after',58),@($clock,'prior_revision',58),@($clock,'revision_after',59),@($clock,'cumulative_weight_before',284),@($clock,'cumulative_weight_after',284),@($pipeline,'synthetic_sender_calls',1),@($pipeline,'exchange_http_attempts',0),@($pipeline,'order_writes',0))) {
            $obj=$spec[0];$name=[string]$spec[1];Receipt-Require (Receipt-Integer $obj.$name ([long]$spec[2])) 'RECEIPT_COUNTER_TYPE_INVALID'
        }
        Receipt-Require ($clock.prior_audit_events -eq 55 -and $clock.audit_events_after -eq 56 -and $pipeline.audit_events_before -eq 56 -and $pipeline.audit_events_after -eq 58 -and $clock.prior_revision -eq 58 -and $clock.revision_after -eq 59 -and $clock.cumulative_weight_before -eq 284 -and $clock.cumulative_weight_after -eq 284 -and $pipeline.synthetic_sender_calls -eq 1 -and $pipeline.exchange_http_attempts -eq 0 -and $pipeline.order_writes -eq 0) 'RECEIPT_COUNTERS_INVALID'
        Receipt-Require ($pipeline.synthetic_copy_evidence_rehearsal -ceq 'PIPELINE_VERIFIED_SYNTHETIC_ONLY_KEEP_STOPPED' -and $pipeline.archive_receipt_sha256 -cmatch '^[0-9a-f]{64}$' -and $pipeline.archive_receipt_sha256 -ceq $pipeline.content_review.account_receipt_sha256) 'RECEIPT_PIPELINE_INVALID'
        foreach ($flag in @('startup_ready_observed','evidence_binding_created','protected_rows_and_files_preserved','shared_journal_changed','resource_checkpoint_changed')) { Receipt-Require (Receipt-Boolean $pipeline.$flag $true) 'RECEIPT_PIPELINE_INVALID' }
        foreach ($flag in @('copy_isolation_independently_verified','exchange_credentials_accessed','financial_ledger_changed','period_baselines_changed','request_states_changed','automatic_retry','installation_ready','execution_approved','orders_enabled','live_trading_enabled')) { Receipt-Require (Receipt-Boolean $pipeline.$flag $false) 'RECEIPT_AUTHORITY_FLAG_INVALID' }
        $clockFile=Receipt-Open (Join-Path $selected.directory 'COPY_CLOCK_RESULT.json');$handles+=,$clockFile.handle;$all+=,$clockFile
        $pipelineFile=Receipt-Open (Join-Path $selected.directory 'COPY_EVIDENCE_RESULT.json');$handles+=,$pipelineFile.handle;$all+=,$pipelineFile
        Receipt-Require (($clock | ConvertTo-Json -Depth 32 -Compress) -ceq ($clockFile.value | ConvertTo-Json -Depth 32 -Compress) -and ($pipeline | ConvertTo-Json -Depth 32 -Compress) -ceq ($pipelineFile.value | ConvertTo-Json -Depth 32 -Compress)) 'RECEIPT_COMPANION_MISMATCH'
        foreach ($item in $all) {
            $item.handle.Position=0;$bytes=New-Object byte[] ([int]$item.handle.Length);$offset=0
            while ($offset -lt $bytes.Length) { $count=$item.handle.Read($bytes,$offset,$bytes.Length-$offset);Receipt-Require ($count -gt 0) 'RECEIPT_READ_FAILED';$offset+=$count }
            Receipt-Require ((Receipt-Digest $bytes) -ceq $item.sha256) 'RECEIPT_CHANGED_DURING_REVIEW'
        }
        return [pscustomobject]@{evidence_receipt_review='STORED_OBJECTS_AND_COMPANIONS_VERIFIED';receipt_sha256=$selected.receipt.sha256;nested_content_review_is_object=$true;remaining_requirements_count=8;companion_json_matches=$true;files_changed=$false;docker_used=$false;services_started=$false;receipt_hash_compared_to_external_pin=$false;remote_account_identity_verified=$false;baseline_adoption_approved=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } finally { foreach ($handle in $handles) { $handle.Dispose() } }
}
