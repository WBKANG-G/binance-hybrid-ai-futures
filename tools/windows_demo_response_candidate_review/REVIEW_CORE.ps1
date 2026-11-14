function Invoke-CandidateImageReview([string]$Root) {
    $stage='PINNED_LOCAL_PRESERVATION_EVIDENCE'; $handles=@(); $helper=$null
    try {
        $config=Read-RestoreJson (Join-Path $Root 'CONFIG.json')
        $backup=Find-RetainedBackup $config; $restore=Find-RestoreEvidence $config
        foreach ($path in @(@('BACKUP_RECEIPT.json','BACKUP_STATE.json','manifest.json','database.tar','runtime.tar','images.tar') | ForEach-Object {Join-Path $backup $_})+@($restore)) { $handles+=,(New-Object IO.FileStream($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)) }
        if ((Restore-Hash $restore) -cne $config.restore_receipt_sha256) { throw 'HANDOFF_RESTORE_HASH_MISMATCH' }
        $receipt=Read-RestoreJson (Join-Path $backup 'BACKUP_RECEIPT.json'); Assert-InputHashes $backup $config $receipt
        $state=Read-RestoreJson (Join-Path $backup 'BACKUP_STATE.json'); $recovery=Read-RestoreJson $restore; Assert-RestoreEvidence $recovery $config
        $original=Assert-HandoffStoppedScope $config $receipt $state $recovery
        $stage='CREATE_NETWORKLESS_IMAGE_ONLY_REVIEW'; Write-Output ('CANDIDATE_REVIEW_STAGE='+$stage)
        $image=$state.canonical_images[0]
        $created=@(Backup-Docker @('create','--pull=never','--interactive','--name',('demo-get-candidate-review-'+[Guid]::NewGuid().ToString('N')),'--label','com.docker.compose.project=demo-review-only','--label','com.docker.compose.service=candidate-review','--network','none','--restart','no','--read-only','--memory','512m','--pids-limit','32','--cap-drop','ALL','--security-opt','no-new-privileges','--user','10001:10001','--entrypoint','python',$image,'-I','-B','-') 1)
        if ($created.Count -ne 1 -or $created[0] -cnotmatch '^[0-9a-f]{64}$' -or @($original | ForEach-Object {$_.id}) -ccontains $created[0]) { throw 'CANDIDATE_HELPER_ID_INVALID' }
        $helper=$created[0]; $item=Read-Container $helper
        $network=@(Backup-Docker @('inspect','--format','{{.HostConfig.NetworkMode}}',$helper) 1)
        if (@($item.mounts).Count -ne 0 -or $network.Count -ne 1 -or $network[0] -cne 'none' -or (Project $item) -cne 'demo-review-only' -or $item.restart.Name -cne 'no' -or -not (Resolve-RestoreImageMatch $item.image $image)) { throw 'CANDIDATE_HELPER_SCOPE_INVALID' }
        Assert-RestoreSourceSnapshot $original @($helper) $false
        $sources=@{}
        foreach ($entry in $config.candidate_files.PSObject.Properties) { $sources[$entry.Name]=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root ('payload/'+$entry.Name)))) }
        $worker=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'worker.py')))
        $encodedConfig=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((@{baseline_files=$config.baseline_files;candidate_files=$config.candidate_files} | ConvertTo-Json -Depth 6 -Compress)))
        $encodedSources=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($sources | ConvertTo-Json -Compress)))
        $stage='IN_MEMORY_IMPORT_AND_DEFAULT_DENIALS'; Write-Output ('CANDIDATE_REVIEW_STAGE='+$stage)
        $payload="import base64,json`nCONFIG=json.loads(base64.b64decode('$encodedConfig'))`nSOURCES=json.loads(base64.b64decode('$encodedSources'))`nexec(compile(base64.b64decode('$worker'),'<image-candidate-review>','exec'))"
        $lines=@($payload | & docker start --attach --interactive $helper 2>$null)
        $exit=$LASTEXITCODE
        if ($lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 4096) { throw 'CANDIDATE_WORKER_OUTPUT_INVALID' }
        $value=$lines[0] | ConvertFrom-Json
        if ($exit -ne 0) {
            if (@('CANDIDATE_BASE_IMAGE_BASELINE_MISMATCH','CANDIDATE_PAYLOAD_HASH_MISMATCH','CANDIDATE_REVIEW_UNAVAILABLE') -ccontains $value.failure_category) { throw $value.failure_category }
            throw 'CANDIDATE_WORKER_OUTPUT_INVALID'
        }
        $keys=@('candidate_review','failure_category','baseline_files_verified','candidate_files_verified','default_denials_verified','network_attempts','database_attempts','filesystem_write_attempts','candidate_installed','database_changed','financial_proof_verified','execution_approved','orders_enabled','live_trading_enabled')
        if ((@($value.PSObject.Properties.Name | Sort-Object) -join '|') -cne (@($keys | Sort-Object) -join '|') -or $value.candidate_review -cne 'BASE_IMAGE_IMPORTS_AND_DEFAULT_DENIALS_VERIFIED' -or $value.failure_category -cne 'NONE') { throw 'CANDIDATE_WORKER_OUTPUT_INVALID' }
        foreach ($flag in @('candidate_installed','database_changed','financial_proof_verified','execution_approved','orders_enabled','live_trading_enabled')) { if ($value.$flag -isnot [bool] -or $value.$flag) { throw 'CANDIDATE_WORKER_OUTPUT_INVALID' } }
        foreach ($name in @('baseline_files_verified','candidate_files_verified','default_denials_verified','network_attempts','database_attempts','filesystem_write_attempts')) { if ($value.$name -isnot [int] -and $value.$name -isnot [long]) { throw 'CANDIDATE_WORKER_OUTPUT_INVALID' } }
        if ($value.baseline_files_verified -ne 89 -or $value.candidate_files_verified -ne 2 -or $value.default_denials_verified -ne 5 -or $value.network_attempts -ne 0 -or $value.database_attempts -ne 0 -or $value.filesystem_write_attempts -ne 0) { throw 'CANDIDATE_WORKER_OUTPUT_INVALID' }
        $running=@(Backup-Docker @('inspect','--format','{{.State.Running}}',$helper) 1)
        if ($running.Count -ne 1 -or $running[0] -cne 'false') { throw 'CANDIDATE_HELPER_NOT_STOPPED' }
        Assert-RestoreSourceSnapshot $original @($helper) $false
        Assert-InputHashes $backup $config $receipt
        if ((Restore-Hash $restore) -cne $config.restore_receipt_sha256) { throw 'HANDOFF_RESTORE_HASH_MISMATCH' }
        [pscustomobject]@{candidate_image_review='TWO_GET_MODULES_COMPATIBLE_DEFAULT_DISABLED';baseline_image_files_verified=89;candidate_files_verified=2;default_denials_verified=5;review_helper_id=$helper;review_helper_stopped=$true;originals_or_restore_copy_started=$false;source_volumes_mounted=$false;candidate_installed=$false;schema_provisioned=$false;binding_created=$false;clock_change_policy_ready=$false;separate_pc_reverification_performed=$false;installation_ready=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;next_action='REVIEW_GET_BOOTSTRAP_AND_CLOCK_CHANGE_POLICY_KEEP_STOPPED'} | ConvertTo-Json -Compress | Write-Output
        Write-Output 'GET_CANDIDATE_IMAGE_REVIEW_PASSED_NOT_INSTALLED_NO_ORDERS'
        return 0
    } catch {
        $reason='CANDIDATE_IMAGE_REVIEW_UNAVAILABLE'
        $allowed=@('CANDIDATE_HELPER_ID_INVALID','CANDIDATE_HELPER_SCOPE_INVALID','CANDIDATE_HELPER_NOT_STOPPED','CANDIDATE_WORKER_OUTPUT_INVALID','CANDIDATE_BASE_IMAGE_BASELINE_MISMATCH','CANDIDATE_PAYLOAD_HASH_MISMATCH','CANDIDATE_REVIEW_UNAVAILABLE','HANDOFF_RESTORE_HASH_MISMATCH','HANDOFF_SCOPE_INVALID','HANDOFF_CONTAINER_NOT_STOPPED','HANDOFF_HELPER_CONFIGURATION_CHANGED','HANDOFF_ORIGINAL_CONFIGURATION_CHANGED','HANDOFF_UNREVIEWED_PROJECT_CONTAINER','HANDOFF_EXTERNAL_SOURCE_CONSUMER','RESTORE_INPUT_HASH_MISMATCH','RESTORE_RETAINED_HASH_MISMATCH','COLD_CONTAINER_SET_CHANGED','COLD_SOURCE_RUNNING_STATE_CHANGED','COLD_SOURCE_CONFIGURATION_CHANGED','COLD_DOCKER_COMMAND_FAILED','BACKUP_PLAN_DOCKER_READ_FAILED')
        if ($allowed -ccontains $_.Exception.Message) { $reason=$_.Exception.Message }
        [pscustomobject]@{candidate_image_review='BLOCKED_KEEP_ORIGINALS_STOPPED';stage=$stage;failure_category=$reason;retained_review_helper_id=$helper;candidate_installed=$false;schema_provisioned=$false;installation_ready=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
        return 2
    } finally { foreach ($handle in $handles) { $handle.Dispose() } }
}
