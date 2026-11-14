$script:CopyDiagnosticId=$null
$script:CopySocket=$null
$script:CopyDiagnostic=$null
$script:CopyDiagnosticFailure=$null
$script:CopyRestoreHandle=$null
function Read-CopySources([object]$Config,[object]$Receipt,[object]$State) {
    $path=Find-RestoreEvidence $Config
    $handle=New-Object IO.FileStream($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ((Restore-Hash $path) -cne $Config.restore_receipt_sha256) { throw 'HANDOFF_RESTORE_HASH_MISMATCH' }
        $recovery=Read-RestoreJson $path; Assert-RestoreEvidence $recovery $Config
        $items=Assert-HandoffStoppedScope $Config $Receipt $State $recovery
        $script:CopyRestoreHandle=$handle
        return ,$items
    } catch { $handle.Dispose(); throw }
}
function Assert-NewClone([string]$Id,[string]$Volume,[object[]]$Original) {
    if (@($Original | ForEach-Object {$_.id}) -ccontains $Id -or [string]::IsNullOrWhiteSpace($script:CopySocket)) { throw 'RESTORE_CLONE_ID_INVALID' }
    $item=Read-Container $Id
    if ($item.restart.Name -cne 'no' -or @($item.mounts).Count -ne 2 -or (Project $item) -cne 'demo-copy-review') { throw 'RESTORE_CLONE_MOUNT_INVALID' }
    foreach ($spec in @(@('/var/lib/postgresql',$Volume),@('/review-socket',$script:CopySocket))) {
        $mount=@($item.mounts | Where-Object {$_.Destination -ceq $spec[0]})
        if ($mount.Count -ne 1 -or $mount[0].Type -cne 'volume' -or $mount[0].Name -cne $spec[1] -or -not $mount[0].RW) { throw 'RESTORE_CLONE_MOUNT_INVALID' }
    }
    $network=@(Backup-Docker @('inspect','--format','{{.HostConfig.NetworkMode}}',$Id) 1)
    if ($network.Count -ne 1 -or $network[0] -cne 'none') { throw 'RESTORE_CLONE_NETWORK_INVALID' }
}
function Invoke-CopyDiagnostic([string]$Root,[object]$Config,[object]$State,[string]$Runtime,[string]$Socket,[object[]]$Original,[object[]]$Owned,[string]$Directory) {
    $created=@(Backup-Docker @('create','--pull=never','--interactive','--label','com.docker.compose.project=demo-copy-review','--label','com.docker.compose.service=copy-diagnostic','--network','none','--restart','no','--read-only','--memory','768m','--pids-limit','32','--cap-drop','ALL','--security-opt','no-new-privileges','--user','10001:10001','--mount',('type=volume,source='+$Runtime+',target=/review-runtime,readonly,volume-nocopy'),'--mount',('type=volume,source='+$Socket+',target=/review-socket,readonly,volume-nocopy'),'--entrypoint','python',$State.canonical_images[0],'-I','-B','-') 1)
    if ($created.Count -ne 1 -or $created[0] -cnotmatch '^[0-9a-f]{64}$' -or @($Original | ForEach-Object {$_.id}) -ccontains $created[0]) { throw 'COPY_HELPER_ID_INVALID' }
    $id=$created[0]; $script:CopyDiagnosticId=$id
    Write-BackupJson (Join-Path $Directory 'COPY_DIAGNOSTIC_RESOURCE.json') @{helper_id=$id;automatic_retry=$false}
    $item=Read-Container $id
    if ((Project $item) -cne 'demo-copy-review' -or @($item.mounts).Count -ne 2 -or $item.restart.Name -cne 'no' -or -not (Resolve-RestoreImageMatch $item.image $State.canonical_images[0])) { throw 'COPY_HELPER_SCOPE_INVALID' }
    foreach ($spec in @(@('/review-runtime',$Runtime),@('/review-socket',$Socket))) {
        $mount=@($item.mounts | Where-Object {$_.Destination -ceq $spec[0]})
        if ($mount.Count -ne 1 -or $mount[0].Type -cne 'volume' -or $mount[0].Name -cne $spec[1] -or $mount[0].RW) { throw 'COPY_HELPER_SCOPE_INVALID' }
    }
    $network=@(Backup-Docker @('inspect','--format','{{.HostConfig.NetworkMode}}',$id) 1)
    if ($network.Count -ne 1 -or $network[0] -cne 'none') { throw 'COPY_HELPER_SCOPE_INVALID' }
    Assert-RestoreSourceSnapshot $Original @($Owned+@($id)) $false
    $sources=@{}
    foreach ($entry in $Config.candidate_files.PSObject.Properties) { $sources[$entry.Name]=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root ('payload/'+$entry.Name)))) }
    $worker=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'copy_worker.py')))
    $cfg=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((@{baseline_files=$Config.baseline_files;candidate_files=$Config.candidate_files} | ConvertTo-Json -Depth 6 -Compress)))
    $src=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($sources | ConvertTo-Json -Compress)))
    $payload="import base64,json`nCONFIG=json.loads(base64.b64decode('$cfg'))`nSOURCES=json.loads(base64.b64decode('$src'))`nexec(compile(base64.b64decode('$worker'),'<copy-diagnostic>','exec'))"
    $lines=@($payload | & docker start --attach --interactive $id 2>$null); $code=$LASTEXITCODE
    if ($lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 12000) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' }
    $value=$lines[0] | ConvertFrom-Json
    if ($code -ne 0) {
        if ($value.failure_category -cmatch '^[A-Z][A-Z0-9_]{1,100}$') { $script:CopyDiagnosticFailure=$value.failure_category }
        throw 'COPY_DIAGNOSTIC_BLOCKED'
    }
    if ((@($value.PSObject.Properties.Name | Sort-Object) -join '|') -cne (@($Config.worker_report_keys | Sort-Object) -join '|') -or $value.copy_transition_review -cne 'ASSESSMENT_COMPLETE_KEEP_STOPPED' -or $value.snapshot_sha256 -cnotmatch '^[0-9a-f]{64}$' -or $value.baseline_files_verified -ne 89 -or $value.candidate_files_verified -ne 3) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' }
    foreach ($flag in @('copy_isolation_independently_verified','writers_stopped_verified','financial_ledger_changed','period_baselines_changed','clock_rebased','request_states_changed','shared_journal_changed','resource_checkpoint_changed','automatic_retry','originals_started_by_review','installation_ready','execution_approved','orders_enabled','live_trading_enabled')) { if ($value.$flag -isnot [bool] -or $value.$flag) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' } }
    foreach ($flag in @('copy_session_read_only','copy_isolation_attested')) { if ($value.$flag -isnot [bool] -or -not $value.$flag) { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' } }
    if ($value.exchange_http_attempts -ne 0 -or $value.order_writes -ne 0 -or $value.historical_unresolved_requests -ne 4 -or $value.stored_day_kst -cne '2026-11-13' -or $value.stored_week_kst -cne '2026-11-09') { throw 'COPY_DIAGNOSTIC_OUTPUT_INVALID' }
    $exit=@(Backup-Docker @('inspect','--format','{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}}',$id) 1)
    if ($exit.Count -ne 1 -or $exit[0] -cne 'false 0 false') { throw 'COPY_HELPER_NOT_STOPPED' }
    $script:CopyDiagnostic=$value
    Write-BackupJson (Join-Path $Directory 'COPY_DIAGNOSTIC.json') $value
    return $id
}
