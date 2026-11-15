$script:EvidencePriorHandle=$null
$script:EvidencePriorPath=$null
function Find-PriorClockReceipt([object]$Config) {
    $base=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
    $folders=@(Get-ChildItem -LiteralPath $base -Directory -Filter 'copy-clock-20261115-*')
    if ($folders.Count -gt 32) { throw 'EVIDENCE_PRIOR_SEARCH_LIMIT' }
    $found=@()
    foreach ($folder in $folders) {
        if (($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'EVIDENCE_PRIOR_PATH_INVALID' }
        $path=Join-Path $folder.FullName 'COPY_CLOCK_RECEIPT.json'
        if ([IO.File]::Exists($path)) {
            Assert-PrivateBackupDirectory $folder.FullName; Assert-PrivateBackupFiles $folder.FullName
            if ((Restore-Hash $path) -ceq $Config.prior_clock_receipt_sha256) { $found+=,$path }
        }
    }
    if ($found.Count -ne 1) { throw 'EVIDENCE_PRIOR_RECEIPT_NOT_UNIQUE' }
    return $found[0]
}
function Read-EvidenceSources([object]$Config,[object]$Receipt,[object]$State) {
    $items=Read-ClockSources $Config $Receipt $State
    $path=Find-PriorClockReceipt $Config
    $handle=New-Object IO.FileStream($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ((Restore-Hash $path) -cne $Config.prior_clock_receipt_sha256) { throw 'EVIDENCE_PRIOR_HASH_MISMATCH' }
        $value=Read-RestoreJson $path
        if ($value.source_reference -cne $Config.prior_clock_source_reference -or $value.prior_copy_receipt_sha256 -cne $Config.prior_copy_receipt_sha256 -or $value.isolated_restore -cne 'COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED' -or $value.backup_manifest_sha256 -cne $Config.manifest_sha256 -or $value.backup_receipt_sha256 -cne $Config.receipt_sha256 -or $value.clone_network -cne 'none') { throw 'EVIDENCE_PRIOR_RECEIPT_INVALID' }
        foreach ($flag in @('copy_mounts_and_network_verified','input_hashes_reverified','clone_stopped','clone_clean_exit_verified','archive_bytes_and_metadata_verified_before_clone_recovery')) { if ($value.$flag -isnot [bool] -or -not $value.$flag) { throw 'EVIDENCE_PRIOR_RECEIPT_INVALID' } }
        foreach ($flag in @('original_services_started','original_volume_mounted_in_restore_helpers','financial_write_attempted','installation_ready','automatic_retry','execution_approved','orders_enabled','live_trading_enabled')) { if ($value.$flag -isnot [bool] -or $value.$flag) { throw 'EVIDENCE_PRIOR_RECEIPT_INVALID' } }
        $clock=$value.copy_clock_result
        foreach ($flag in @('clock_rebased','startup_hold_verified','prior_database_rows_preserved','prior_files_preserved','copy_read_sessions_read_only','copy_write_session_explicit')) { if ($clock.$flag -isnot [bool] -or -not $clock.$flag) { throw 'EVIDENCE_PRIOR_CLOCK_INVALID' } }
        foreach ($flag in @('financial_ledger_changed','period_baselines_changed','request_states_changed','automatic_retry','installation_ready','execution_approved','orders_enabled','live_trading_enabled')) { if ($clock.$flag -isnot [bool] -or $clock.$flag) { throw 'EVIDENCE_PRIOR_CLOCK_INVALID' } }
        $diag=$clock.diagnostic_after
        if ($clock.prior_audit_events -ne $script:ClockPrior.copy_transition_diagnostic.prior_audit_events -or $diag.prior_audit_events -ne $clock.audit_events_after -or $diag.snapshot_sha256 -cne $clock.after_snapshot_sha256 -or $diag.boot_matches -isnot [bool] -or -not $diag.boot_matches -or $clock.baseline_files_verified -ne 89 -or $clock.candidate_files_verified -ne 5 -or $clock.startup_hold_seconds -lt 70.001) { throw 'EVIDENCE_PRIOR_CLOCK_INVALID' }
        if ($clock.copy_clock_rehearsal -cne 'CLOCK_REBASED_HOLD_VERIFIED_KEEP_STOPPED' -or $clock.before_snapshot_sha256 -cne $script:ClockPrior.copy_transition_diagnostic.snapshot_sha256 -or $clock.after_snapshot_sha256 -cnotmatch '^[0-9a-f]{64}$' -or $clock.audit_events_after -ne ($clock.prior_audit_events+1) -or $clock.revision_after -ne ($clock.prior_revision+1) -or $clock.cumulative_weight_after -ne $clock.cumulative_weight_before -or $clock.exchange_http_attempts -ne 0 -or $clock.order_writes -ne 0 -or $diag.clock_matches -isnot [bool] -or -not $diag.clock_matches -or $diag.v2_bound -isnot [bool] -or $diag.v2_bound -or $diag.pending_read_attempts -ne 0 -or $diag.pending_evidence_requests -ne 0 -or $diag.historical_unresolved_requests -ne 4 -or $diag.stored_day_kst -cne '2026-11-13' -or $diag.stored_week_kst -cne '2026-11-09') { throw 'EVIDENCE_PRIOR_CLOCK_INVALID' }
        if (@($value.retained_helper_ids).Count -ne 3 -or @($value.retained_clone_volumes).Count -ne 3 -or $value.clone_container_id -cne $value.retained_helper_ids[1] -or @($value.retained_helper_ids | Select-Object -Unique).Count -ne 3 -or @($value.retained_clone_volumes | Select-Object -Unique).Count -ne 3) { throw 'EVIDENCE_PRIOR_RESOURCES_INVALID' }
        $forbidden=@($State.database_volume,$State.runtime_volume,$Receipt.output_volume)+@($script:ClockPrior.retained_clone_volumes)
        foreach ($name in $value.retained_clone_volumes) { if ($name -cnotmatch '^demo-copy-review-(database|runtime|socket)-[0-9a-f]{32}$' -or $forbidden -ccontains $name) { throw 'EVIDENCE_PRIOR_RESOURCES_INVALID' } }
        for ($i=0;$i -lt 3;$i++) {
            $id=$value.retained_helper_ids[$i]
            if ($id -cnotmatch '^[0-9a-f]{64}$') { throw 'EVIDENCE_PRIOR_RESOURCES_INVALID' }
            $selected=@($items | Where-Object {$_.id -ceq $id})
            if ($selected.Count -ne 1) { throw 'EVIDENCE_PRIOR_RESOURCES_INVALID' }
            $item=$selected[0]; $project='demo-copy-review'; if ($i -eq 2) { $project='demo-copy-clock' }
            if ($item.running -or $item.paused -or $item.dead -or $item.restarting -or $item.restart.Name -cne 'no' -or $item.restart.MaximumRetryCount -ne 0 -or (Project $item) -cne $project) { throw 'EVIDENCE_PRIOR_HELPER_NOT_STOPPED' }
            $image=$State.canonical_images[0]; if ($i -eq 1) { $image=$State.canonical_images[1] }
            if (-not (Resolve-RestoreImageMatch $item.image $image)) { throw 'EVIDENCE_PRIOR_RESOURCES_INVALID' }
            if ($i -eq 0) { $specs=@(@('/bundle','bind','',$false),@('/clone/database','volume',$value.retained_clone_volumes[0],$true),@('/clone/runtime','volume',$value.retained_clone_volumes[1],$true),@('/review-socket','volume',$value.retained_clone_volumes[2],$true)) }
            elseif ($i -eq 1) { $specs=@(@('/var/lib/postgresql','volume',$value.retained_clone_volumes[0],$true),@('/review-socket','volume',$value.retained_clone_volumes[2],$true)) }
            else { $specs=@(@('/review-runtime','volume',$value.retained_clone_volumes[1],$true),@('/review-socket','volume',$value.retained_clone_volumes[2],$false)) }
            if (@($item.mounts).Count -ne $specs.Count) { throw 'EVIDENCE_PRIOR_RESOURCES_INVALID' }
            foreach ($spec in $specs) {
                $mount=@($item.mounts | Where-Object {$_.Destination -ceq $spec[0]})
                if ($mount.Count -ne 1 -or $mount[0].Type -cne $spec[1] -or $mount[0].RW -ne $spec[3] -or ($spec[1] -ceq 'volume' -and $mount[0].Name -cne $spec[2])) { throw 'EVIDENCE_PRIOR_RESOURCES_INVALID' }
            }
            $network=@(Backup-Docker @('inspect','--format','{{.HostConfig.NetworkMode}}',$id) 1)
            $exit=@(Backup-Docker @('inspect','--format','{{.State.ExitCode}} {{.State.OOMKilled}}',$id) 1)
            if ($network.Count -ne 1 -or $network[0] -cne 'none' -or $exit.Count -ne 1 -or $exit[0] -cne '0 false') { throw 'EVIDENCE_PRIOR_RESOURCES_INVALID' }
        }
        $script:EvidencePriorHandle=$handle; $script:EvidencePriorPath=$path
        return ,$items
    } catch { $handle.Dispose(); throw }
}
function Invoke-EvidenceDiagnostic([string]$Root,[object]$Config,[object]$State,[string]$Runtime,[string]$Socket,[object[]]$Original,[object[]]$Owned,[string]$Directory) {
    $id=Invoke-ClockDiagnostic $Root $Config $State $Runtime $Socket $Original $Owned $Directory
    $pipeline=$script:CopyDiagnostic.synthetic_evidence_rehearsal
    if ($pipeline.synthetic_copy_evidence_rehearsal -cne 'PIPELINE_VERIFIED_SYNTHETIC_ONLY_KEEP_STOPPED' -or $pipeline.synthetic_sender_calls -ne 1 -or $pipeline.exchange_http_attempts -ne 0 -or $pipeline.order_writes -ne 0 -or $pipeline.audit_events_before -ne $script:CopyDiagnostic.audit_events_after -or $pipeline.audit_events_after -ne ($pipeline.audit_events_before+2) -or $pipeline.before_snapshot_sha256 -cne $script:CopyDiagnostic.after_snapshot_sha256 -or $pipeline.after_snapshot_sha256 -cnotmatch '^[0-9a-f]{64}$' -or $pipeline.archive_receipt_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'EVIDENCE_RESULT_INVALID' }
    foreach ($flag in @('startup_ready_observed','evidence_binding_created','protected_rows_and_files_preserved','shared_journal_changed','resource_checkpoint_changed')) { if ($pipeline.$flag -isnot [bool] -or -not $pipeline.$flag) { throw 'EVIDENCE_RESULT_INVALID' } }
    foreach ($flag in @('copy_isolation_independently_verified','exchange_credentials_accessed','financial_ledger_changed','period_baselines_changed','request_states_changed','automatic_retry','installation_ready','execution_approved','orders_enabled','live_trading_enabled')) { if ($pipeline.$flag -isnot [bool] -or $pipeline.$flag) { throw 'EVIDENCE_RESULT_INVALID' } }
    $content=$pipeline.content_review
    if ($content.positive_usdt_wallet_observed -isnot [bool] -or -not $content.positive_usdt_wallet_observed -or $content.asset_rows_checked -ne 1 -or $content.position_rows_checked -ne 1 -or $content.response_body_sha256 -cnotmatch '^[0-9a-f]{64}$' -or @($content.remaining_requirements).Count -ne 8) { throw 'EVIDENCE_CONTENT_RESULT_INVALID' }
    if ($content.account_content_review -cne 'NARROW_FLAT_USDT_ARITHMETIC_CONSISTENT' -or $content.account_receipt_sha256 -cne $pipeline.archive_receipt_sha256) { throw 'EVIDENCE_CONTENT_RESULT_INVALID' }
    foreach ($flag in @('proposal_input_shape_complete','caller_claims_present','referenced_proofs_verified','operator_statement_independently_verified','whole_history_coverage_verified','midnight_valuation_verified','stored_periods_verified_against_ledger','account_configuration_verified','open_orders_verified','remote_account_identity_verified','baseline_amount_proposed','baseline_source_accepted','baseline_adoption_approved','execution_approved','orders_enabled','live_trading_enabled')) { if ($content.$flag -isnot [bool] -or $content.$flag) { throw 'EVIDENCE_CONTENT_RESULT_INVALID' } }
    if ((Restore-Hash $script:EvidencePriorPath) -cne $Config.prior_clock_receipt_sha256) { throw 'EVIDENCE_PRIOR_HASH_MISMATCH' }
    Write-BackupJson (Join-Path $Directory 'COPY_EVIDENCE_RESULT.json') $pipeline
    return $id
}
