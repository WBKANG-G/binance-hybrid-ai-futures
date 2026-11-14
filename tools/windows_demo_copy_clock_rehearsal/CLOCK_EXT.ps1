$script:ClockApplyAttempted=$false
$script:ClockPriorHandle=$null
$script:ClockPriorPath=$null
$script:ClockPrior=$null
function Find-PriorCopyReceipt([object]$Config) {
    $base=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
    $folders=@(Get-ChildItem -LiteralPath $base -Directory -Filter 'copy-review-20261115-*')
    if ($folders.Count -gt 32) { throw 'CLOCK_PRIOR_SEARCH_LIMIT' }
    $found=@()
    foreach ($folder in $folders) {
        if (($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'CLOCK_PRIOR_PATH_INVALID' }
        $path=Join-Path $folder.FullName 'COPY_REVIEW_RECEIPT.json'
        if ([IO.File]::Exists($path)) {
            Assert-PrivateBackupDirectory $folder.FullName; Assert-PrivateBackupFiles $folder.FullName
            if ((Restore-Hash $path) -ceq $Config.prior_copy_receipt_sha256) { $found+=,$path }
        }
    }
    if ($found.Count -ne 1) { throw 'CLOCK_PRIOR_RECEIPT_NOT_UNIQUE' }
    return $found[0]
}
function Read-ClockSources([object]$Config,[object]$Receipt,[object]$State) {
    $items=Read-CopySources $Config $Receipt $State
    $path=Find-PriorCopyReceipt $Config
    $handle=New-Object IO.FileStream($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ((Restore-Hash $path) -cne $Config.prior_copy_receipt_sha256) { throw 'CLOCK_PRIOR_HASH_MISMATCH' }
        $value=Read-RestoreJson $path
        if ($value.source_reference -cne $Config.prior_copy_source_reference -or $value.isolated_restore -cne 'COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED' -or $value.backup_manifest_sha256 -cne $Config.manifest_sha256 -or $value.backup_receipt_sha256 -cne $Config.receipt_sha256 -or $value.clone_network -cne 'none') { throw 'CLOCK_PRIOR_RECEIPT_INVALID' }
        foreach ($flag in @('copy_mounts_and_network_verified','input_hashes_reverified','clone_stopped','clone_clean_exit_verified','archive_bytes_and_metadata_verified_before_clone_recovery')) { if ($value.$flag -isnot [bool] -or -not $value.$flag) { throw 'CLOCK_PRIOR_RECEIPT_INVALID' } }
        foreach ($flag in @('original_services_started','original_volume_mounted_in_restore_helpers','financial_write_attempted','installation_ready','automatic_retry','execution_approved','orders_enabled','live_trading_enabled')) { if ($value.$flag -isnot [bool] -or $value.$flag) { throw 'CLOCK_PRIOR_RECEIPT_INVALID' } }
        $diag=$value.copy_transition_diagnostic
        if ($diag.copy_transition_review -cne 'ASSESSMENT_COMPLETE_KEEP_STOPPED' -or $diag.snapshot_sha256 -cnotmatch '^[0-9a-f]{64}$' -or $diag.v2_bound -isnot [bool] -or $diag.v2_bound -or $diag.clock_matches -isnot [bool] -or $diag.clock_matches -or $diag.pending_read_attempts -ne 0 -or $diag.pending_evidence_requests -ne 0 -or $diag.historical_unresolved_requests -ne 4 -or $diag.stored_day_kst -cne '2026-11-13' -or $diag.stored_week_kst -cne '2026-11-09') { throw 'CLOCK_PRIOR_DIAGNOSTIC_INVALID' }
        if (@($value.retained_helper_ids).Count -ne 3 -or @($value.retained_clone_volumes).Count -ne 3 -or $value.clone_container_id -cne $value.retained_helper_ids[1]) { throw 'CLOCK_PRIOR_RESOURCES_INVALID' }
        $forbidden=@($State.database_volume,$State.runtime_volume,$Receipt.output_volume)
        foreach ($name in $value.retained_clone_volumes) { if ($name -cnotmatch '^demo-copy-review-(database|runtime|socket)-[0-9a-f]{32}$' -or $forbidden -ccontains $name) { throw 'CLOCK_PRIOR_RESOURCES_INVALID' } }
        if (@($value.retained_clone_volumes | Select-Object -Unique).Count -ne 3 -or @($value.retained_helper_ids | Select-Object -Unique).Count -ne 3) { throw 'CLOCK_PRIOR_RESOURCES_INVALID' }
        for ($i=0;$i -lt 3;$i++) {
            $id=$value.retained_helper_ids[$i]
            if ($id -cnotmatch '^[0-9a-f]{64}$') { throw 'CLOCK_PRIOR_RESOURCES_INVALID' }
            $selected=@($items | Where-Object {$_.id -ceq $id})
            if ($selected.Count -ne 1) { throw 'CLOCK_PRIOR_RESOURCES_INVALID' }
            $item=$selected[0]
            if ($item.running -or $item.paused -or $item.dead -or $item.restarting -or $item.restart.Name -cne 'no' -or $item.restart.MaximumRetryCount -ne 0 -or (Project $item) -cne 'demo-copy-review') { throw 'CLOCK_PRIOR_HELPER_NOT_STOPPED' }
            $image=$State.canonical_images[0]; if ($i -eq 1) { $image=$State.canonical_images[1] }
            if (-not (Resolve-RestoreImageMatch $item.image $image)) { throw 'CLOCK_PRIOR_RESOURCES_INVALID' }
            if ($i -eq 0) { $specs=@(@('/bundle','bind','',$false),@('/clone/database','volume',$value.retained_clone_volumes[0],$true),@('/clone/runtime','volume',$value.retained_clone_volumes[1],$true),@('/review-socket','volume',$value.retained_clone_volumes[2],$true)) }
            elseif ($i -eq 1) { $specs=@(@('/var/lib/postgresql','volume',$value.retained_clone_volumes[0],$true),@('/review-socket','volume',$value.retained_clone_volumes[2],$true)) }
            else { $specs=@(@('/review-runtime','volume',$value.retained_clone_volumes[1],$false),@('/review-socket','volume',$value.retained_clone_volumes[2],$false)) }
            if (@($item.mounts).Count -ne $specs.Count) { throw 'CLOCK_PRIOR_RESOURCES_INVALID' }
            foreach ($spec in $specs) {
                $mount=@($item.mounts | Where-Object {$_.Destination -ceq $spec[0]})
                if ($mount.Count -ne 1 -or $mount[0].Type -cne $spec[1] -or $mount[0].RW -ne $spec[3] -or ($spec[1] -ceq 'volume' -and $mount[0].Name -cne $spec[2])) { throw 'CLOCK_PRIOR_RESOURCES_INVALID' }
            }
            $network=@(Backup-Docker @('inspect','--format','{{.HostConfig.NetworkMode}}',$id) 1)
            $exit=@(Backup-Docker @('inspect','--format','{{.State.ExitCode}} {{.State.OOMKilled}}',$id) 1)
            if ($network.Count -ne 1 -or $network[0] -cne 'none' -or $exit.Count -ne 1 -or $exit[0] -cne '0 false') { throw 'CLOCK_PRIOR_RESOURCES_INVALID' }
        }
        $script:ClockPriorHandle=$handle; $script:ClockPriorPath=$path; $script:ClockPrior=$value
        return ,$items
    } catch { $handle.Dispose(); throw }
}
function Assert-ClockHelper([string]$Id,[string]$Runtime,[string]$Socket,[object[]]$Original,[string]$Image) {
    if (@($Original | ForEach-Object {$_.id}) -ccontains $Id) { throw 'COPY_HELPER_ID_INVALID' }
    $item=Read-Container $Id
    if ((Project $item) -cne 'demo-copy-clock' -or @($item.mounts).Count -ne 2 -or $item.restart.Name -cne 'no' -or -not (Resolve-RestoreImageMatch $item.image $Image)) { throw 'COPY_HELPER_SCOPE_INVALID' }
    foreach ($spec in @(@('/review-runtime',$Runtime,$true),@('/review-socket',$Socket,$false))) {
        $mount=@($item.mounts | Where-Object {$_.Destination -ceq $spec[0]})
        if ($mount.Count -ne 1 -or $mount[0].Type -cne 'volume' -or $mount[0].Name -cne $spec[1] -or $mount[0].RW -ne $spec[2]) { throw 'COPY_HELPER_SCOPE_INVALID' }
    }
    $network=@(Backup-Docker @('inspect','--format','{{.HostConfig.NetworkMode}}',$Id) 1)
    if ($network.Count -ne 1 -or $network[0] -cne 'none') { throw 'COPY_HELPER_SCOPE_INVALID' }
}
function Invoke-ClockDiagnostic([string]$Root,[object]$Config,[object]$State,[string]$Runtime,[string]$Socket,[object[]]$Original,[object[]]$Owned,[string]$Directory) {
    $created=@(Backup-Docker @('create','--pull=never','--interactive','--label','com.docker.compose.project=demo-copy-clock','--label','com.docker.compose.service=clock-rehearsal','--network','none','--restart','no','--read-only','--memory','768m','--pids-limit','32','--cap-drop','ALL','--security-opt','no-new-privileges','--user','10001:10001','--mount',('type=volume,source='+$Runtime+',target=/review-runtime,volume-nocopy'),'--mount',('type=volume,source='+$Socket+',target=/review-socket,readonly,volume-nocopy'),'--entrypoint','python',$State.canonical_images[0],'-I','-B','-') 1)
    if ($created.Count -ne 1 -or $created[0] -cnotmatch '^[0-9a-f]{64}$') { throw 'COPY_HELPER_ID_INVALID' }
    $id=$created[0]; $script:CopyDiagnosticId=$id
    Write-BackupJson (Join-Path $Directory 'COPY_CLOCK_RESOURCE.json') @{helper_id=$id;copy_may_change=$true;automatic_retry=$false}
    Assert-ClockHelper $id $Runtime $Socket $Original $State.canonical_images[0]
    Assert-RestoreSourceSnapshot $Original @($Owned+@($id)) $false
    $sources=@{}
    foreach ($entry in $Config.candidate_files.PSObject.Properties) { $sources[$entry.Name]=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root ('payload/'+$entry.Name)))) }
    $worker=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'copy_worker.py')))
    $cfg=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((@{baseline_files=$Config.baseline_files;candidate_files=$Config.candidate_files;expected_snapshot_sha256=$script:ClockPrior.copy_transition_diagnostic.snapshot_sha256} | ConvertTo-Json -Depth 6 -Compress)))
    $src=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($sources | ConvertTo-Json -Compress)))
    $payload="import base64,json`nCONFIG=json.loads(base64.b64decode('$cfg'))`nSOURCES=json.loads(base64.b64decode('$src'))`nexec(compile(base64.b64decode('$worker'),'<copy-clock>','exec'))"
    $start=New-Object Diagnostics.ProcessStartInfo
    $start.FileName='docker'; $start.Arguments='start --attach --interactive '+$id
    $start.UseShellExecute=$false; $start.CreateNoWindow=$true; $start.RedirectStandardInput=$true; $start.RedirectStandardOutput=$true; $start.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process; $process.StartInfo=$start
    $watch=[Diagnostics.Stopwatch]::StartNew(); $lines=@(); $progressCount=0
    try {
        $script:ClockApplyAttempted=$true
        if (-not $process.Start()) { throw 'CLOCK_WORKER_START_FAILED' }
        $errors=$process.StandardError.ReadToEndAsync()
        $process.StandardInput.WriteLine($payload); $process.StandardInput.Close()
        while ($true) {
            $task=$process.StandardOutput.ReadLineAsync()
            while (-not $task.IsCompleted) {
                if ($watch.Elapsed.TotalSeconds -gt 480) { throw 'CLOCK_WORKER_TIMEOUT' }
                Start-Sleep -Milliseconds 200
            }
            $line=$task.GetAwaiter().GetResult()
            if ($null -eq $line) { break }
            if ($line -cmatch '^COPY_CLOCK_HOLD remaining_seconds=[0-9]{1,2}$') {
                $progressCount++; if ($progressCount -gt 40) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' }
                Write-Host $line
            } else {
                if ($line.Length -gt 12000 -or $lines.Count -ne 0) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' }
                $lines+=,$line
            }
        }
        if (-not $process.WaitForExit(5000)) { throw 'CLOCK_WORKER_TIMEOUT' }
        $code=$process.ExitCode; $stderr=$errors.GetAwaiter().GetResult()
        if (-not [string]::IsNullOrEmpty($stderr) -or $lines.Count -ne 1) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' }
    } catch {
        Assert-ClockHelper $id $Runtime $Socket $Original $State.canonical_images[0]
        $running=@(Backup-Docker @('inspect','--format','{{.State.Running}}',$id) 1)
        if ($running.Count -eq 1 -and $running[0] -ceq 'true') { $null=Backup-Docker @('kill','--signal=SIGTERM',$id) 1 }
        throw
    } finally { $process.Dispose() }
    $value=$lines[0] | ConvertFrom-Json
    if ($code -ne 0) {
        if ($value.failure_category -cmatch '^[A-Z][A-Z0-9_]{1,100}$') { $script:CopyDiagnosticFailure=$value.failure_category }
        throw 'COPY_DIAGNOSTIC_BLOCKED'
    }
    if ((@($value.PSObject.Properties.Name | Sort-Object) -join '|') -cne (@($Config.worker_report_keys | Sort-Object) -join '|') -or $value.copy_clock_rehearsal -cne 'CLOCK_REBASED_HOLD_VERIFIED_KEEP_STOPPED' -or $value.before_snapshot_sha256 -cne $script:ClockPrior.copy_transition_diagnostic.snapshot_sha256 -or $value.baseline_files_verified -ne 89 -or $value.candidate_files_verified -ne 5 -or $value.prior_audit_events -ne $script:ClockPrior.copy_transition_diagnostic.prior_audit_events -or $value.audit_events_after -ne ($value.prior_audit_events+1) -or $value.revision_after -ne ($value.prior_revision+1) -or $value.cumulative_weight_after -ne $value.cumulative_weight_before) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' }
    foreach ($flag in @('financial_ledger_changed','period_baselines_changed','request_states_changed','automatic_retry','installation_ready','execution_approved','orders_enabled','live_trading_enabled')) { if ($value.$flag -isnot [bool] -or $value.$flag) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' } }
    foreach ($flag in @('clock_rebased','startup_hold_verified','prior_database_rows_preserved','prior_files_preserved','copy_read_sessions_read_only','copy_write_session_explicit')) { if ($value.$flag -isnot [bool] -or -not $value.$flag) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' } }
    $diag=$value.diagnostic_after
    if ($diag.clock_matches -isnot [bool] -or -not $diag.clock_matches -or $diag.v2_bound -isnot [bool] -or $diag.v2_bound -or $diag.historical_unresolved_requests -ne 4 -or $diag.stored_day_kst -cne '2026-11-13' -or $diag.stored_week_kst -cne '2026-11-09' -or $value.exchange_http_attempts -ne 0 -or $value.order_writes -ne 0) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' }
    $exit=@(Backup-Docker @('inspect','--format','{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}}',$id) 1)
    if ($exit.Count -ne 1 -or $exit[0] -cne 'false 0 false') { throw 'COPY_HELPER_NOT_STOPPED' }
    if ((Restore-Hash $script:ClockPriorPath) -cne $Config.prior_copy_receipt_sha256) { throw 'CLOCK_PRIOR_HASH_MISMATCH' }
    $script:CopyDiagnostic=$value
    Write-BackupJson (Join-Path $Directory 'COPY_CLOCK_RESULT.json') $value
    return $id
}
