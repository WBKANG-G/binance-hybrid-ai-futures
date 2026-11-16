$script:PrivateTimingReasons=@()
$script:PriorPrivateStoppedVerified=$false
$script:PrivateHelperIDs=@()
$script:PrivateKeyVolume=$null
$script:PrivateCreation=@{}
$script:PrivateEmptyVerified=$false
$script:PrivateRecordSequence=0
function Get-PrivateKstDate { return [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow,'Korea Standard Time').ToString('yyyy-MM-dd') }
$script:PrivatePriorHandle=$null
$script:PrivatePriorPath=$null
function Find-PriorEvidenceReceipt([object]$Config) {
    $base=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
    $folders=@(Get-ChildItem -LiteralPath $base -Directory -Filter 'copy-evidence-20261115-*')
    if ($folders.Count -gt 32) { throw 'PRIVATE_PRIOR_SEARCH_LIMIT' }
    $found=@()
    foreach ($folder in $folders) {
        if (($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'PRIVATE_PRIOR_PATH_INVALID' }
        $path=Join-Path $folder.FullName 'COPY_EVIDENCE_RECEIPT.json'
        if ([IO.File]::Exists($path)) {
            Assert-PrivateBackupDirectory $folder.FullName; Assert-PrivateBackupFiles $folder.FullName
            if ((Restore-Hash $path) -ceq $Config.prior_evidence_receipt_sha256) { $found+=,$path }
        }
    }
    if ($found.Count -ne 1) { throw 'PRIVATE_PRIOR_RECEIPT_NOT_UNIQUE' }
    return $found[0]
}
function Read-PrivateSourcesV1([object]$Config,[object]$Receipt,[object]$State) {
    if ((Get-PrivateKstDate) -cne '2026-11-16') { throw 'PRIVATE_REVIEW_DATE_MISMATCH' }
    $items=Read-EvidenceSources $Config $Receipt $State
    $path=Find-PriorEvidenceReceipt $Config
    $handle=New-Object IO.FileStream($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ((Restore-Hash $path) -cne $Config.prior_evidence_receipt_sha256) { throw 'PRIVATE_PRIOR_HASH_MISMATCH' }
        $value=Read-RestoreJson $path
        if ($value.source_reference -cne $Config.prior_evidence_source_reference -or $value.prior_copy_receipt_sha256 -cne $Config.prior_copy_receipt_sha256 -or $value.isolated_restore -cne 'COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED' -or $value.backup_manifest_sha256 -cne $Config.manifest_sha256 -or $value.backup_receipt_sha256 -cne $Config.receipt_sha256 -or $value.clone_network -cne 'none') { throw 'PRIVATE_PRIOR_RECEIPT_INVALID' }
        foreach ($flag in @('copy_mounts_and_network_verified','input_hashes_reverified','clone_stopped','clone_clean_exit_verified','archive_bytes_and_metadata_verified_before_clone_recovery')) { if ($value.$flag -isnot [bool] -or -not $value.$flag) { throw 'PRIVATE_PRIOR_RECEIPT_INVALID' } }
        foreach ($flag in @('original_services_started','original_volume_mounted_in_restore_helpers','financial_write_attempted','installation_ready','automatic_retry','execution_approved','orders_enabled','live_trading_enabled')) { if ($value.$flag -isnot [bool] -or $value.$flag) { throw 'PRIVATE_PRIOR_RECEIPT_INVALID' } }
        if ($value.prior_clock_receipt_sha256 -cne $Config.prior_clock_receipt_sha256) { throw 'PRIVATE_PRIOR_CLOCK_PIN_INVALID' }
        $clock=$value.copy_evidence_result
        foreach ($flag in @('clock_rebased','startup_hold_verified','prior_database_rows_preserved','prior_files_preserved','copy_read_sessions_read_only','copy_write_session_explicit')) { if ($clock.$flag -isnot [bool] -or -not $clock.$flag) { throw 'PRIVATE_PRIOR_CLOCK_INVALID' } }
        foreach ($flag in @('financial_ledger_changed','period_baselines_changed','request_states_changed','automatic_retry','installation_ready','execution_approved','orders_enabled','live_trading_enabled')) { if ($clock.$flag -isnot [bool] -or $clock.$flag) { throw 'PRIVATE_PRIOR_CLOCK_INVALID' } }
        $diag=$clock.diagnostic_after
        if ($clock.prior_audit_events -ne $script:ClockPrior.copy_transition_diagnostic.prior_audit_events -or $diag.prior_audit_events -ne $clock.audit_events_after -or $diag.snapshot_sha256 -cne $clock.after_snapshot_sha256 -or $diag.boot_matches -isnot [bool] -or -not $diag.boot_matches -or $clock.baseline_files_verified -ne 89 -or $clock.candidate_files_verified -ne 10 -or $clock.startup_hold_seconds -lt 70.001) { throw 'PRIVATE_PRIOR_CLOCK_INVALID' }
        if ($clock.copy_clock_rehearsal -cne 'CLOCK_REBASED_HOLD_VERIFIED_KEEP_STOPPED' -or $clock.before_snapshot_sha256 -cne $script:ClockPrior.copy_transition_diagnostic.snapshot_sha256 -or $clock.after_snapshot_sha256 -cnotmatch '^[0-9a-f]{64}$' -or $clock.audit_events_after -ne ($clock.prior_audit_events+1) -or $clock.revision_after -ne ($clock.prior_revision+1) -or $clock.cumulative_weight_after -ne $clock.cumulative_weight_before -or $clock.exchange_http_attempts -ne 0 -or $clock.order_writes -ne 0 -or $diag.clock_matches -isnot [bool] -or -not $diag.clock_matches -or $diag.v2_bound -isnot [bool] -or $diag.v2_bound -or $diag.pending_read_attempts -ne 0 -or $diag.pending_evidence_requests -ne 0 -or $diag.historical_unresolved_requests -ne 4 -or $diag.stored_day_kst -cne '2026-11-13' -or $diag.stored_week_kst -cne '2026-11-09') { throw 'PRIVATE_PRIOR_CLOCK_INVALID' }
        $pipeline=$clock.synthetic_evidence_rehearsal
        if ($pipeline.synthetic_copy_evidence_rehearsal -cne 'PIPELINE_VERIFIED_SYNTHETIC_ONLY_KEEP_STOPPED' -or $pipeline.synthetic_sender_calls -ne 1 -or $pipeline.exchange_http_attempts -ne 0 -or $pipeline.audit_events_before -ne $clock.audit_events_after -or $pipeline.audit_events_after -ne ($pipeline.audit_events_before+2) -or $pipeline.content_review -isnot [pscustomobject] -or $pipeline.content_review.remaining_requirements -isnot [array] -or @($pipeline.content_review.remaining_requirements).Count -ne 8) { throw 'PRIVATE_PRIOR_PIPELINE_INVALID' }
        if (@($value.retained_helper_ids).Count -ne 3 -or @($value.retained_clone_volumes).Count -ne 3 -or $value.clone_container_id -cne $value.retained_helper_ids[1] -or @($value.retained_helper_ids | Select-Object -Unique).Count -ne 3 -or @($value.retained_clone_volumes | Select-Object -Unique).Count -ne 3) { throw 'PRIVATE_PRIOR_RESOURCES_INVALID' }
        $forbidden=@($State.database_volume,$State.runtime_volume,$Receipt.output_volume)+@($script:ClockPrior.retained_clone_volumes)
        foreach ($name in $value.retained_clone_volumes) { if ($name -cnotmatch '^demo-copy-review-(database|runtime|socket)-[0-9a-f]{32}$' -or $forbidden -ccontains $name) { throw 'PRIVATE_PRIOR_RESOURCES_INVALID' } }
        for ($i=0;$i -lt 3;$i++) {
            $id=$value.retained_helper_ids[$i]
            if ($id -cnotmatch '^[0-9a-f]{64}$') { throw 'PRIVATE_PRIOR_RESOURCES_INVALID' }
            $selected=@($items | Where-Object {$_.id -ceq $id})
            if ($selected.Count -ne 1) { throw 'PRIVATE_PRIOR_RESOURCES_INVALID' }
            $item=$selected[0]; $project='demo-copy-review'; if ($i -eq 2) { $project='demo-copy-evidence' }
            if ($item.running -or $item.paused -or $item.dead -or $item.restarting -or $item.restart.Name -cne 'no' -or $item.restart.MaximumRetryCount -ne 0 -or (Project $item) -cne $project) { throw 'PRIVATE_PRIOR_HELPER_NOT_STOPPED' }
            $image=$State.canonical_images[0]; if ($i -eq 1) { $image=$State.canonical_images[1] }
            if (-not (Resolve-RestoreImageMatch $item.image $image)) { throw 'PRIVATE_PRIOR_RESOURCES_INVALID' }
            if ($i -eq 0) { $specs=@(@('/bundle','bind','',$false),@('/clone/database','volume',$value.retained_clone_volumes[0],$true),@('/clone/runtime','volume',$value.retained_clone_volumes[1],$true),@('/review-socket','volume',$value.retained_clone_volumes[2],$true)) }
            elseif ($i -eq 1) { $specs=@(@('/var/lib/postgresql','volume',$value.retained_clone_volumes[0],$true),@('/review-socket','volume',$value.retained_clone_volumes[2],$true)) }
            else { $specs=@(@('/review-runtime','volume',$value.retained_clone_volumes[1],$true),@('/review-socket','volume',$value.retained_clone_volumes[2],$false)) }
            if (@($item.mounts).Count -ne $specs.Count) { throw 'PRIVATE_PRIOR_RESOURCES_INVALID' }
            foreach ($spec in $specs) {
                $mount=@($item.mounts | Where-Object {$_.Destination -ceq $spec[0]})
                if ($mount.Count -ne 1 -or $mount[0].Type -cne $spec[1] -or $mount[0].RW -ne $spec[3] -or ($spec[1] -ceq 'volume' -and $mount[0].Name -cne $spec[2])) { throw 'PRIVATE_PRIOR_RESOURCES_INVALID' }
            }
            $network=@(Backup-Docker @('inspect','--format','{{.HostConfig.NetworkMode}}',$id) 1)
            $exit=@(Backup-Docker @('inspect','--format','{{.State.ExitCode}} {{.State.OOMKilled}}',$id) 1)
            if ($network.Count -ne 1 -or $network[0] -cne 'none' -or $exit.Count -ne 1 -or $exit[0] -cne '0 false') { throw 'PRIVATE_PRIOR_RESOURCES_INVALID' }
        }
        $script:PrivatePriorHandle=$handle; $script:PrivatePriorPath=$path
        return ,$items
    } catch { $handle.Dispose(); throw }
}
function Private-ReadInspect([string]$Id) {
    $lines=@(Backup-Docker @('inspect','--format','{{json .}}',$Id) 1)
    if ($lines.Count -ne 1) { throw 'PRIVATE_INSPECT_INVALID' }
    return ($lines[0] | ConvertFrom-Json)
}
function Private-SecurityJson([object]$Item) {
    $hostRecord=[ordered]@{}
    foreach ($name in @('NetworkMode','ReadonlyRootfs','Privileged','CapDrop','CapAdd','RestartPolicy','SecurityOpt','PidMode','IpcMode','Devices','PortBindings','PublishAllPorts','Tmpfs','LogConfig','GroupAdd','Memory','PidsLimit')) {
        $property=$Item.HostConfig.PSObject.Properties[$name];if ($null -eq $property) {$hostRecord[$name]=$null} else {$hostRecord[$name]=$property.Value}
    }
    return (Restore-CanonicalMetadataJson ([pscustomobject]@{image=$Item.Image;user=$Item.Config.User;cmd=$Item.Config.Cmd;entrypoint=$Item.Config.Entrypoint;env=$Item.Config.Env;labels=$Item.Config.Labels;host=[pscustomobject]$hostRecord;mounts=@($Item.Mounts | Sort-Object -Property Destination)}))
}
function Private-AssertManaged([string]$Id,[object[]]$Original,[string]$Image) {
    if (@($Original | ForEach-Object {$_.id}) -ccontains $Id -or -not $script:PrivateCreation.ContainsKey($Id)) { throw 'PRIVATE_HELPER_ID_INVALID' }
    $item=Private-ReadInspect $Id
    if ($item.Id -cne $Id -or -not (Resolve-RestoreImageMatch $item.Image $Image) -or $item.Config.Labels.'com.docker.compose.project' -cne 'demo-read-private' -or (Private-SecurityJson $item) -cne (Private-SecurityJson $script:PrivateCreation[$Id])) { throw 'PRIVATE_HELPER_CONTEXT_CHANGED' }
    return $item
}
function Private-Record([string]$Directory,[switch]$Final) {
    $name='PRIVATE_RESOURCES_{0:D4}.json' -f $script:PrivateRecordSequence
    $script:PrivateRecordSequence++
    if ($Final) {$name='PRIVATE_RESOURCES.json'}
    Write-BackupJson (Join-Path $Directory $name) @{private_helper_ids=$script:PrivateHelperIDs;private_key_volume=$script:PrivateKeyVolume;synthetic_keys_only=$true;private_key_session_destroyed=$script:PrivateEmptyVerified;automatic_retry=$false}
}
function Private-Create([string]$Role,[string[]]$Mounts,[string]$Image,[object[]]$Original,[string]$Directory) {
    $createArgs=@('create','--pull=never','--interactive','--network','none','--restart','no','--read-only','--memory','512m','--pids-limit','64','--cap-drop','ALL','--security-opt','no-new-privileges','--log-driver','none','--user','10001:10001','--label','com.docker.compose.project=demo-read-private','--label',('com.docker.compose.service='+$Role))
    foreach ($mount in $Mounts) {$createArgs+=@('--mount',$mount)}
    $createArgs+=@('--entrypoint','python',$Image,'-I','-B')
    if ($Role -ceq 'key-holder') {$createArgs+=@('-c','import signal; signal.pause()')} else {$createArgs+=,'-'}
    $created=@(Backup-Docker $createArgs 1)
    if ($created.Count -ne 1 -or $created[0] -cnotmatch '^[0-9a-f]{64}$' -or @($Original | ForEach-Object {$_.id}) -ccontains $created[0] -or $script:PrivateHelperIDs -ccontains $created[0]) { throw 'PRIVATE_HELPER_ID_INVALID' }
    $id=$created[0];$script:PrivateHelperIDs+=,$id
    $script:PrivateCreation[$id]=Private-ReadInspect $id
    Private-Record $Directory
    $null=Private-AssertManaged $id $Original $Image
    return $id
}
function Private-RunWorker([string]$Id,[string]$Role,[string]$Root,[object]$Config,[object]$Scope,[object[]]$Original,[string]$Image) {
    $null=Private-AssertManaged $Id $Original $Image
    $sources=@{};foreach ($entry in $Config.candidate_files.PSObject.Properties) {$sources[$entry.Name]=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root ('payload/'+$entry.Name))))}
    $cfg=@{baseline_files=$Config.baseline_files;candidate_files=$Config.candidate_files;expected_snapshot_sha256=$script:ClockPrior.copy_transition_diagnostic.snapshot_sha256;role=$Role;scope_policy=$Scope;review_day_kst=$Config.review_day_kst}
    $encoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($cfg | ConvertTo-Json -Depth 32 -Compress)))
    $src=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($sources | ConvertTo-Json -Compress)))
    $worker=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'private_worker.py')))
    $payload="import base64,json`nCONFIG=json.loads(base64.b64decode('$encoded'))`nSOURCES=json.loads(base64.b64decode('$src'))`nexec(compile(base64.b64decode('$worker'),'<private-week-review>','exec'))"
    $start=New-Object Diagnostics.ProcessStartInfo;$start.FileName='docker';$start.Arguments='start --attach --interactive '+$Id
    $start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.RedirectStandardInput=$true;$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process;$process.StartInfo=$start;$lines=@();$watch=[Diagnostics.Stopwatch]::StartNew();$progressCount=0
    try {
        if (-not $process.Start()) { throw 'PRIVATE_WORKER_START_FAILED' }
        $errorTask=$process.StandardError.ReadToEndAsync();$process.StandardInput.WriteLine($payload);$process.StandardInput.Close()
        while ($true) {
            $task=$process.StandardOutput.ReadLineAsync()
            while (-not $task.IsCompleted) {if ($watch.Elapsed.TotalSeconds -gt 480) {throw 'PRIVATE_WORKER_TIMEOUT'};Start-Sleep -Milliseconds 200}
            $line=$task.GetAwaiter().GetResult();if ($null -eq $line) {break}
            if ($line -cmatch '^COPY_CLOCK_HOLD remaining_seconds=[0-9]{1,2}$' -or $line -cmatch '^PRIVATE_COPY_STAGE=(PRIVATE_KEY_AND_WEEK_REVIEW|VERIFY_REHEARSED_COPY|CONFIGURE_COPY_AUXILIARY_ONLY|SYNTHETIC_GET_AND_ARCHIVE|READ_ONLY_CONTENT_AND_PRESERVATION_CHECK)$') {$progressCount++;if ($progressCount -gt 40) {throw 'PRIVATE_WORKER_OUTPUT_INVALID'};Write-Host $line}
            else {if ($line.Length -gt 32768 -or $lines.Count -ne 0) {throw 'PRIVATE_WORKER_OUTPUT_INVALID'};$lines+=,$line}
        }
        if (-not $process.WaitForExit(5000)) {throw 'PRIVATE_WORKER_TIMEOUT'}
        $stderr=$errorTask.GetAwaiter().GetResult()
        if (-not [string]::IsNullOrEmpty($stderr) -or $lines.Count -ne 1) {throw 'PRIVATE_WORKER_OUTPUT_INVALID'}
        $value=$lines[0] | ConvertFrom-Json
        if ($process.ExitCode -ne 0) {
            if ($null -ne $value.PSObject.Properties['private_timing_reasons']) {
                $allowedReasons=@('PRIVATE_READ_CLOCK_INVALID','PRIVATE_READ_MONOTONIC_ROLLBACK','PRIVATE_READ_LEASE_DEADLINE_EXCEEDED','PRIVATE_READ_SESSION_EXPIRED_OR_FUTURE','PRIVATE_READ_BOOT_CHANGED','PRIVATE_READ_CLOCK_DIVERGED')
                $reasons=@($value.private_timing_reasons)
                if ($reasons.Count -gt 6) {throw 'PRIVATE_WORKER_OUTPUT_INVALID'}
                foreach ($reason in $reasons) {if ($reason -isnot [string] -or $allowedReasons -cnotcontains $reason) {throw 'PRIVATE_WORKER_OUTPUT_INVALID'}}
                $script:PrivateTimingReasons=$reasons
            }
if ($value.failure_category -cmatch '^[A-Z][A-Z0-9_]{1,100}$') {$script:CopyDiagnosticFailure=$value.failure_category};throw 'PRIVATE_WORKER_BLOCKED'}
        $item=Private-AssertManaged $Id $Original $Image
        if ($item.State.Running -or $item.State.ExitCode -ne 0 -or $item.State.OOMKilled) {throw 'PRIVATE_WORKER_NOT_STOPPED'}
        return $value
    } finally {$process.Dispose()}
}
function Private-Stop([string]$Id,[object[]]$Original,[string]$Image) {
    $item=Private-AssertManaged $Id $Original $Image
    if ($item.State.Running) {
        $null=Backup-Docker @('kill','--signal=SIGINT',$Id) 1
        $watch=[Diagnostics.Stopwatch]::StartNew()
        while ($watch.Elapsed.TotalSeconds -lt 10) {
            $item=Private-AssertManaged $Id $Original $Image
            if (-not $item.State.Running -and -not $item.State.Restarting) {return}
            Start-Sleep -Milliseconds 200
        }
        $null=Private-AssertManaged $Id $Original $Image
        $null=Backup-Docker @('kill','--signal=SIGKILL',$Id) 1
    }
    $item=Private-AssertManaged $Id $Original $Image
    if ($item.State.Running -or $item.State.Restarting) {throw 'PRIVATE_HELPER_STOP_FAILED'}
}
function Assert-PrivateWeekResult([object]$Value,[object]$Config) {
    if ((@($Value.PSObject.Properties.Name | Sort-Object) -join '|') -cne (@($Config.worker_report_keys | Sort-Object) -join '|') -or $Value.baseline_files_verified -ne 89 -or $Value.candidate_files_verified -ne 16 -or $Value.before_snapshot_sha256 -cne $script:ClockPrior.copy_transition_diagnostic.snapshot_sha256 -or $Value.copy_clock_rehearsal -cne 'CLOCK_REBASED_HOLD_VERIFIED_KEEP_STOPPED' -or $Value.audit_events_after -ne ($Value.prior_audit_events+1) -or $Value.revision_after -ne ($Value.prior_revision+1) -or $Value.cumulative_weight_after -ne $Value.cumulative_weight_before) {throw 'PRIVATE_RESULT_INVALID'}
    if ($Value.startup_hold_seconds -lt 70.001 -or $Value.exchange_http_attempts -ne 0 -or $Value.order_writes -ne 0 -or $Value.after_snapshot_sha256 -cnotmatch '^[0-9a-f]{64}$' -or $Value.protected_database_sha256 -cnotmatch '^[0-9a-f]{64}$') {throw 'PRIVATE_RESULT_INVALID'}
    foreach ($flag in @('clock_rebased','startup_hold_verified','prior_database_rows_preserved','prior_files_preserved','copy_read_sessions_read_only','copy_write_session_explicit','synthetic_key_file_unchanged')) {if ($Value.$flag -isnot [bool] -or -not $Value.$flag) {throw 'PRIVATE_RESULT_INVALID'}}
    foreach ($flag in @('financial_ledger_changed','period_baselines_changed','request_states_changed','automatic_retry','installation_ready','execution_approved','orders_enabled','live_trading_enabled')) {if ($Value.$flag -isnot [bool] -or $Value.$flag) {throw 'PRIVATE_RESULT_INVALID'}}
    $week=$Value.private_week_review;$pipeline=$Value.synthetic_evidence_rehearsal
    if ($week.private_week_copy_review -cne 'SYNTHETIC_PRIVATE_COPY_VERIFIED_PERIODS_UNCHANGED' -or $week.review_day_kst -cne '2026-11-16' -or $week.review_week_kst -cne '2026-11-16' -or $week.stored_day_kst -cne '2026-11-13' -or $week.stored_week_kst -cne '2026-11-09' -or (Restore-CanonicalMetadataJson $week.pipeline) -cne (Restore-CanonicalMetadataJson $pipeline)) {throw 'PRIVATE_WEEK_RESULT_INVALID'}
    foreach ($flag in @('day_and_week_evidence_still_required','week_boundary_acknowledged_for_observation_only')) {if ($week.$flag -isnot [bool] -or -not $week.$flag) {throw 'PRIVATE_WEEK_RESULT_INVALID'}}
    foreach ($flag in @('operator_attestation_received','baseline_source_accepted','baseline_adoption_approved','daily_baseline_changed','weekly_baseline_changed','remote_account_identity_verified','installation_ready','execution_approved','orders_enabled','live_trading_enabled')) {if ($week.$flag -isnot [bool] -or $week.$flag) {throw 'PRIVATE_WEEK_RESULT_INVALID'}}
    if ($pipeline.synthetic_copy_evidence_rehearsal -cne 'PIPELINE_VERIFIED_SYNTHETIC_ONLY_KEEP_STOPPED' -or $pipeline.synthetic_sender_calls -ne 1 -or $pipeline.exchange_http_attempts -ne 0 -or $pipeline.order_writes -ne 0 -or $pipeline.audit_events_before -ne $Value.audit_events_after -or $pipeline.audit_events_after -ne ($pipeline.audit_events_before+2)) {throw 'PRIVATE_PIPELINE_RESULT_INVALID'}
    foreach ($flag in @('private_tmpfs_credentials_used','startup_ready_observed','evidence_binding_created','protected_rows_and_files_preserved','shared_journal_changed','resource_checkpoint_changed')) {if ($pipeline.$flag -isnot [bool] -or -not $pipeline.$flag) {throw 'PRIVATE_PIPELINE_RESULT_INVALID'}}
    foreach ($flag in @('copy_isolation_independently_verified','exchange_credentials_accessed','financial_ledger_changed','period_baselines_changed','request_states_changed','automatic_retry','installation_ready','execution_approved','orders_enabled','live_trading_enabled')) {if ($pipeline.$flag -isnot [bool] -or $pipeline.$flag) {throw 'PRIVATE_PIPELINE_RESULT_INVALID'}}
    if ($pipeline.private_lease_opened_at_capture -isnot [bool] -or -not $pipeline.private_lease_opened_at_capture -or ($pipeline.private_preparation_seconds -isnot [double] -and $pipeline.private_preparation_seconds -isnot [int] -and $pipeline.private_preparation_seconds -isnot [long]) -or $pipeline.private_preparation_seconds -lt 0 -or $pipeline.private_preparation_seconds -ge 300) {throw 'PRIVATE_LATE_LEASE_RESULT_INVALID'}
    $content=$pipeline.content_review
    if ($content -isnot [pscustomobject] -or $content.remaining_requirements -isnot [array] -or @($content.remaining_requirements).Count -ne 8 -or $content.account_content_review -cne 'NARROW_FLAT_USDT_ARITHMETIC_CONSISTENT') {throw 'PRIVATE_CONTENT_RESULT_INVALID'}
    foreach ($flag in @('proposal_input_shape_complete','caller_claims_present','referenced_proofs_verified','operator_statement_independently_verified','whole_history_coverage_verified','midnight_valuation_verified','stored_periods_verified_against_ledger','account_configuration_verified','open_orders_verified','remote_account_identity_verified','baseline_amount_proposed','baseline_source_accepted','baseline_adoption_approved','execution_approved','orders_enabled','live_trading_enabled')) {if ($content.$flag -isnot [bool] -or $content.$flag) {throw 'PRIVATE_CONTENT_RESULT_INVALID'}}
    foreach ($flag in @('network_enabled','docker_socket_mounted','copy_isolation_independently_verified','remote_account_identity_verified','execution_approved','orders_enabled','live_trading_enabled')) {if ($Value.private_scope_policy.$flag -isnot [bool] -or $Value.private_scope_policy.$flag) {throw 'PRIVATE_SCOPE_RESULT_INVALID'}}
    if ($Value.private_scope_policy.private_read_scope -cne 'OWNED_RO_KEY_AND_FRESH_COPY_MOUNTS_CHECKED' -or $Value.private_scope_policy.network_enabled -or $Value.private_scope_policy.docker_socket_mounted) {throw 'PRIVATE_SCOPE_RESULT_INVALID'}
}
function Invoke-PrivateDiagnostic([string]$Root,[object]$Config,[object]$State,[string]$Runtime,[string]$Socket,[object[]]$Original,[object[]]$Owned,[string]$Directory) {
    $errorRecord=$null;$scope=$null;$reader=$null;$registrar=$null;$holder=$null
    try {
        Write-Host 'PRIVATE_COPY_STAGE=CREATE_NEW_SYNTHETIC_TMPFS'
        $script:PrivateKeyVolume='demo-read-private-keys-'+[Guid]::NewGuid().ToString('N')
        $oldVolumes=@(Backup-Docker @('volume','ls','--format','{{.Name}}') 10000)
        if ($oldVolumes -ccontains $script:PrivateKeyVolume) {throw 'PRIVATE_KEY_VOLUME_ALREADY_EXISTS'}
        $created=@(Backup-Docker @('volume','create','--driver','local','--opt','type=tmpfs','--opt','device=tmpfs','--opt','o=size=65536,mode=0700,uid=10001,gid=10001,noexec,nosuid,nodev',$script:PrivateKeyVolume) 1)
        if ($created.Count -ne 1 -or $created[0] -cne $script:PrivateKeyVolume) {throw 'PRIVATE_KEY_VOLUME_INVALID'}
        Private-Record $Directory
        $holder=Private-Create 'key-holder' @('type=volume,source='+$script:PrivateKeyVolume+',target=/run/demo-keys,readonly,volume-nocopy') $State.canonical_images[0] $Original $Directory
        $registrar=Private-Create 'registrar' @('type=volume,source='+$script:PrivateKeyVolume+',target=/run/demo-keys,volume-nocopy') $State.canonical_images[0] $Original $Directory
        $reader=Private-Create 'read-helper' @(('type=volume,source='+$script:PrivateKeyVolume+',target=/run/demo-keys,readonly,volume-nocopy'),('type=volume,source='+$Runtime+',target=/review-runtime,volume-nocopy'),('type=volume,source='+$Socket+',target=/review-socket,readonly,volume-nocopy')) $State.canonical_images[0] $Original $Directory
        $script:CopyDiagnosticId=$reader
        $forbidden=@();foreach ($item in $Original) {foreach ($mount in $item.mounts) {if ($mount.Type -ceq 'volume') {$forbidden+=,$mount.Name}}}
        foreach ($id in $Owned) {$obj=Private-ReadInspect $id;foreach ($mount in $obj.Mounts) {if ($mount.Destination -ceq '/var/lib/postgresql') {$forbidden+=,$mount.Name}}}
        $scope=[pscustomobject]@{holder=$script:PrivateCreation[$holder];reader=$script:PrivateCreation[$reader];registrar=$script:PrivateCreation[$registrar];image=$script:PrivateCreation[$holder].Image;keys_volume=$script:PrivateKeyVolume;runtime_volume=$Runtime;socket_volume=$Socket;owned_container_ids=@($holder,$reader,$registrar);forbidden_container_ids=@($Original | ForEach-Object {$_.id});forbidden_volume_names=@($forbidden | Select-Object -Unique)}
        Write-BackupJson (Join-Path $Directory 'PRIVATE_SCOPE.json') $scope
        Assert-RestoreSourceSnapshot $Original @($Owned+$script:PrivateHelperIDs) $false
        $null=Backup-Docker @('start',$holder) 1
        $registered=Private-RunWorker $registrar 'REGISTER' $Root $Config $scope $Original $State.canonical_images[0]
        if ($registered.private_key_session -cne 'SYNTHETIC_REGISTERED_IN_NEW_TMPFS' -or $registered.real_keys_used -isnot [bool] -or $registered.real_keys_used) {throw 'PRIVATE_REGISTRATION_RESULT_INVALID'}
        Write-Host 'PRIVATE_COPY_STAGE=REHEARSE_COPY_AND_PRIVATE_SIGNING'
        $script:ClockApplyAttempted=$true
        $value=Private-RunWorker $reader 'READ' $Root $Config $scope $Original $State.canonical_images[0]
        Assert-PrivateWeekResult $value $Config
        $script:CopyDiagnostic=$value
        Write-BackupJson (Join-Path $Directory 'PRIVATE_WEEK_RESULT.json') $value
        Assert-RestoreSourceSnapshot $Original @($Owned+$script:PrivateHelperIDs) $false
    } catch {$errorRecord=$_}
    finally {
        try {
            $allStopped=$true
            foreach ($id in @($reader,$registrar,$holder)) {if ($null -ne $id) {try {Private-Stop $id $Original $State.canonical_images[0]} catch {$allStopped=$false;if ($null -eq $errorRecord) {$errorRecord=$_}}}}
            if ($allStopped -and $null -ne $scope -and $null -ne $registrar) {
                try {
                    $probe=Private-RunWorker $registrar 'PROBE_EMPTY' $Root $Config $scope $Original $State.canonical_images[0]
                    if ($probe.private_key_session -ceq 'EMPTY_AFTER_LAST_HOLDER_STOP' -and $probe.files_created -is [bool] -and -not $probe.files_created) {$script:PrivateEmptyVerified=$true}
                } finally {Private-Stop $registrar $Original $State.canonical_images[0]}
            }
        } catch {if ($null -eq $errorRecord) {$errorRecord=$_}}
        Private-Record $Directory -Final
    }
    if ($null -ne $errorRecord) {throw $errorRecord}
    if (-not $script:PrivateEmptyVerified) {throw 'PRIVATE_KEY_DESTRUCTION_NOT_VERIFIED'}
    if ((Restore-Hash $script:PrivatePriorPath) -cne $Config.prior_evidence_receipt_sha256) {throw 'PRIVATE_PRIOR_HASH_MISMATCH'}
    return $reader
}

function Private-PriorMount([object]$Item,[string]$Path,[bool]$RW) {
    $found=@($Item.Mounts | Where-Object {$_.Destination -ceq $Path})
    if ($found.Count -ne 1 -or $found[0].Type -cne 'volume' -or $found[0].RW -isnot [bool] -or $found[0].RW -ne $RW) {throw 'PRIVATE_PRIOR_FAILED_MOUNT_INVALID'}
    return $found[0].Name
}
function Assert-PriorPrivateStopped([object]$Config,[object]$State) {
    $ids=@($Config.prior_private_helper_ids)
    if ($ids.Count -ne 5 -or @($ids | Select-Object -Unique).Count -ne 5) {throw 'PRIVATE_PRIOR_FAILED_IDS_INVALID'}
    $roles=@('archive-restore','copy-db','key-holder','registrar','read-helper')
    $objects=@()
    for ($i=0;$i -lt 5;$i++) {
        $id=$ids[$i]
        if ($id -isnot [string] -or $id -cnotmatch '^[0-9a-f]{64}$' -or @($Config.api_container_id,$Config.database_container_id) -ccontains $id) {throw 'PRIVATE_PRIOR_FAILED_IDS_INVALID'}
        $item=Private-ReadInspect $id
        $project='demo-copy-review';if ($i -gt 1) {$project='demo-read-private'}
        $image=$State.canonical_images[0];if ($i -eq 1) {$image=$State.canonical_images[1]}
        if ($item.Id -cne $id -or $item.State.Running -or $item.State.Restarting -or $item.State.Paused -or $item.State.Dead -or $item.State.OOMKilled -or $item.State.Status -cne 'exited' -or $item.HostConfig.RestartPolicy.Name -cne 'no' -or $item.HostConfig.RestartPolicy.MaximumRetryCount -ne 0 -or $item.HostConfig.NetworkMode -cne 'none' -or $item.Config.Labels.'com.docker.compose.project' -cne $project -or $item.Config.Labels.'com.docker.compose.service' -cne $roles[$i] -or -not (Resolve-RestoreImageMatch $item.Image $image)) {throw 'PRIVATE_PRIOR_FAILED_NOT_STOPPED_OR_CHANGED'}
        if ($i -gt 1 -and ($item.Config.User -cne '10001:10001' -or -not $item.HostConfig.ReadonlyRootfs -or $item.HostConfig.LogConfig.Type -cne 'none')) {throw 'PRIVATE_PRIOR_FAILED_NOT_STOPPED_OR_CHANGED'}
        $objects+=,$item
    }
    $reader=$objects[4]
    if (@($reader.Mounts).Count -ne 3) {throw 'PRIVATE_PRIOR_FAILED_MOUNT_INVALID'}
    $keys=Private-PriorMount $reader '/run/demo-keys' $false
    $runtime=Private-PriorMount $reader '/review-runtime' $true
    $socket=Private-PriorMount $reader '/review-socket' $false
    $database=Private-PriorMount $objects[1] '/var/lib/postgresql' $true
    if ($keys -cnotmatch '^demo-read-private-keys-[0-9a-f]{32}$' -or $runtime -cnotmatch '^demo-copy-review-runtime-[0-9a-f]{32}$' -or $socket -cnotmatch '^demo-copy-review-socket-[0-9a-f]{32}$' -or $database -cnotmatch '^demo-copy-review-database-[0-9a-f]{32}$' -or @($keys,$runtime,$socket,$database | Select-Object -Unique).Count -ne 4) {throw 'PRIVATE_PRIOR_FAILED_MOUNT_INVALID'}
    foreach ($v in @($keys,$runtime,$socket,$database)) {if (@($State.database_volume,$State.runtime_volume) -ccontains $v) {throw 'PRIVATE_PRIOR_FAILED_MOUNT_INVALID'}}
    if (@($objects[1].Mounts).Count -ne 2 -or (Private-PriorMount $objects[1] '/review-socket' $true) -cne $socket -or @($objects[2].Mounts).Count -ne 1 -or (Private-PriorMount $objects[2] '/run/demo-keys' $false) -cne $keys -or @($objects[3].Mounts).Count -ne 1 -or (Private-PriorMount $objects[3] '/run/demo-keys' $true) -cne $keys) {throw 'PRIVATE_PRIOR_FAILED_MOUNT_INVALID'}
    if (@($objects[0].Mounts).Count -ne 4 -or (Private-PriorMount $objects[0] '/clone/database' $true) -cne $database -or (Private-PriorMount $objects[0] '/clone/runtime' $true) -cne $runtime -or (Private-PriorMount $objects[0] '/review-socket' $true) -cne $socket) {throw 'PRIVATE_PRIOR_FAILED_MOUNT_INVALID'}
    $bind=@($objects[0].Mounts | Where-Object {$_.Destination -ceq '/bundle'})
    if ($bind.Count -ne 1 -or $bind[0].Type -cne 'bind' -or $bind[0].RW) {throw 'PRIVATE_PRIOR_FAILED_MOUNT_INVALID'}
    $script:PriorPrivateStoppedVerified=$true
}
function Read-PrivateSources([object]$Config,[object]$Receipt,[object]$State) {
    $items=Read-PrivateSourcesV1 $Config $Receipt $State
    Assert-PriorPrivateStopped $Config $State
    return ,$items
}
