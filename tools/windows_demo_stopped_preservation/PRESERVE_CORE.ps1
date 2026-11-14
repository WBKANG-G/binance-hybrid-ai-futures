# This path never executes a source container, signals it, or changes restart policies.
function Read-StoppedScope([object]$Config) {
    $original=@(@(Container-Ids) | ForEach-Object {Read-Container $_})
    $project=@($original | Where-Object {(Project $_) -ceq 'hybrid-windows'})
    if ($project.Count -ne 7) { throw 'PRESERVE_SCOPE_CHANGED' }
    $roles=@{api=3;db=1;alerts=1;web=1;'key-session'=1}
    foreach ($role in $roles.Keys) {
        if (@($project | Where-Object {(Service $_) -ceq $role}).Count -ne $roles[$role]) { throw 'PRESERVE_SCOPE_CHANGED' }
    }
    foreach ($item in $project) {
        if ($item.running -or $item.paused -or $item.restarting -or $item.dead -or $item.restart.Name -cne 'no' -or $item.restart.MaximumRetryCount -ne 0) { throw 'PRESERVE_SOURCE_NOT_STOPPED' }
    }
    $api=@($project | Where-Object {$_.id -ceq $Config.api_container_id -and $_.name -ceq '/hybrid-windows-api-1' -and (Service $_) -ceq 'api'})
    $db=@($project | Where-Object {$_.id -ceq $Config.database_container_id -and (Service $_) -ceq 'db'})
    if ($api.Count -ne 1 -or $db.Count -ne 1) { throw 'PRESERVE_SCOPE_CHANGED' }
    $api=$api[0]; $db=$db[0]
    $runtime=Volume-Mount $api '/app/demo-export-state'
    if (@($db.mounts).Count -ne 1) { throw 'PRESERVE_SCOPE_CHANGED' }
    $database=Volume-Mount $db '/var/lib/postgresql'
    if ($database.Name -ceq $runtime.Name -or (Uses-Source ([pscustomobject]@{mounts=@($database)}) $runtime)) { throw 'PRESERVE_SCOPE_CHANGED' }
    $dbUsers=0; $runtimeUsers=0
    foreach ($item in $original) {
        $d=Uses-Source $item $database; $r=Uses-Source $item $runtime
        if ($d) { $dbUsers++ }; if ($r) { $runtimeUsers++ }
        if ($d -or $r) {
            if ((Project $item) -cne 'hybrid-windows' -or (Service $item) -ceq 'key-session' -or $item.running) { throw 'PRESERVE_EXTERNAL_CONSUMER' }
            if ($d -and $item.id -cne $db.id) { throw 'PRESERVE_SCOPE_CHANGED' }
            if ($r -and (Service $item) -cne 'api') { throw 'PRESERVE_SCOPE_CHANGED' }
        }
    }
    if ($dbUsers -ne 1 -or $runtimeUsers -ne 3) { throw 'PRESERVE_SCOPE_CHANGED' }
    foreach ($item in @($project | Where-Object {(Service $_) -ceq 'api'})) {
        foreach ($mount in @($item.mounts)) {
            if ($mount.Destination.StartsWith('/app/demo-export-state/',[StringComparison]::Ordinal)) { throw 'PRESERVE_SCOPE_CHANGED' }
            if ($mount.Destination -ceq '/run/demo-keys') {
                $key=[pscustomobject]@{mounts=@($mount)}
                if ((Uses-Source $key $database) -or (Uses-Source $key $runtime)) { throw 'PRESERVE_SCOPE_CHANGED' }
            }
        }
    }
    Assert-SourceSnapshot $original @() $false
    return [pscustomobject]@{original=$original;api=$api;db=$db;database=$database;runtime=$runtime}
}
function Invoke-StoppedPreservation([string]$Root,[switch]$ExecutePreservation,[switch]$CheckOnly) {
    $stage='OPT_IN'; $directory=$null; $helpers=@(); $outputVolume=$null; $mutex=$null; $locked=$false; $imagesSaved=$false
    try {
        if (($ExecutePreservation -and $CheckOnly) -or (-not $ExecutePreservation -and -not $CheckOnly)) { throw 'PRESERVE_EXPLICIT_MODE_REQUIRED' }
        $config=Get-Content -Raw -LiteralPath (Join-Path $Root 'CONFIG.json') | ConvertFrom-Json
        $stage='STOPPED_SOURCE_METADATA'; Write-Output ('PRESERVATION_STAGE='+$stage)
        $scope=Read-StoppedScope $config
        $original=$scope.original; $api=$scope.api; $db=$scope.db; $database=$scope.database; $runtime=$scope.runtime
        if ($CheckOnly) {
            [pscustomobject]@{stopped_preservation='STOPPED_SCOPE_OBSERVED';source_containers_started=$false;source_bytes_read=$false;backup_created=$false;clean_shutdown_verified=$false;account_pause_rechecked=$false;automatic_retry=$false;order_writes=0;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
            Write-Output 'STOPPED_SCOPE_CHECKED_NO_SOURCE_START_NO_BACKUP_NO_ORDERS'
            return 0
        }
        $stage='PRIVATE_LOCAL_STORAGE'
        $mutex=New-Object Threading.Mutex($false,'Global\WBKANG_DEMO_COLD_BACKUP')
        $locked=$mutex.WaitOne(0)
        if (-not $locked) { throw 'COLD_ANOTHER_BACKUP_ACTIVE' }
        $token=[Guid]::NewGuid().ToString('N')
        $directory=New-PrivateBackupDirectory $token
        $stage='SAVE_PINNED_IMAGES'; Write-Output ('BACKUP_STAGE='+$stage)
        $imageIds=@(); $imageBytes=0L
        foreach ($reference in @($api.image,$db.image)) {
            $line=@(Backup-Docker @('image','inspect','--format','{{.Id}} {{.Size}}',$reference) 1)
            if ($line.Count -ne 1 -or $line[0] -cnotmatch '^(sha256:[0-9a-f]{64}) ([0-9]{1,12})$') { throw 'COLD_IMAGE_ID_INVALID' }
            $imageIds+=,$Matches[1]; $imageBytes += [long]$Matches[2]
        }
        if ((Backup-FreeBytes $directory) -lt ($imageBytes*2+1GB)) { throw 'COLD_HOST_SPACE_INSUFFICIENT' }
        Assert-PrivateBackupDirectory $directory
        $null=Backup-Docker @('image','save','--output',(Join-Path $directory 'images.tar'),$imageIds[0],$imageIds[1]) 0
        Assert-PrivateBackupFiles $directory; Sync-BackupFile (Join-Path $directory 'images.tar')
        $imageHash=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $directory 'images.tar')).Hash.ToLowerInvariant(); $imagesSaved=$true
        Assert-SourceSnapshot $original @() $false
        $stage='RETAIN_SOURCE_METADATA'
        # Environment variables and key-session container/mount are excluded.
        $retained=@($original | Where-Object {(Project $_) -ceq 'hybrid-windows' -and (Service $_) -cne 'key-session'} | ForEach-Object {
            [pscustomobject]@{id=$_.id;image=$_.image;service=(Service $_);restart=$_.restart;mounts=@($_.mounts | Where-Object {$_.Destination -cne '/run/demo-keys'})}
        })
        Write-BackupJson (Join-Path $directory 'BACKUP_STATE.json') @{protocol='STOPPED_UNCLEAN_PRESERVATION_V1';source_reference=$config.source_reference;source_snapshots=$retained;pgdata_relative='18/docker';database_volume=$database.Name;runtime_volume=$runtime.Name;canonical_images=$imageIds;clean_shutdown_verified=$false;account_pause_rechecked=$false;key_session_excluded=$true;automatic_resume_authorized=$false}
        $stage='CREATE_COLD_ARCHIVES'; Write-Output ('BACKUP_STAGE='+$stage)
        $outputVolume='demo-cold-backup-'+$token
        $volume=@(Backup-Docker @('volume','create',$outputVolume) 1)
        if ($volume.Count -ne 1 -or $volume[0] -cne $outputVolume) { throw 'COLD_OUTPUT_VOLUME_INVALID' }
        $worker=@(Backup-Docker @('create','--pull=never','--interactive','--network','none','--restart','no','--read-only','--memory','512m','--pids-limit','32','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--security-opt','no-new-privileges','--user','0:0','--mount',('type=volume,source='+$database.Name+',target=/source/database,readonly,volume-nocopy'),'--mount',('type=volume,source='+$runtime.Name+',target=/source/runtime,readonly,volume-nocopy'),'--mount',('type=volume,source='+$outputVolume+',target=/output,volume-nocopy'),'--entrypoint','python',$imageIds[0],'-I','-B','-') 1)
        if ($worker.Count -ne 1 -or $worker[0] -cnotmatch '^[0-9a-f]{64}$') { throw 'COLD_HELPER_ID_INVALID' }; $helpers+=,$worker[0]
        $archive=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'cold_archive.py')))
        $driver=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'preserve_driver.py')))
        $workerConfig=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((@{pgdata_relative='18/docker';postgresql_major=18;preserve_unclean_stopped_source=$true} | ConvertTo-Json -Compress)))
        $payload="import base64,json`nARCHIVE_SOURCE=base64.b64decode('$archive')`nCONFIG=json.loads(base64.b64decode('$workerConfig'))`nexec(compile(base64.b64decode('$driver'),'<cold-backup-driver>','exec'))"
        $lines=@($payload | & docker start --attach --interactive $worker[0] 2>$null)
        $workerExit=$LASTEXITCODE
        if ($lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 4096) { throw 'COLD_ARCHIVE_WORKER_FAILED' }
        $created=$lines[0] | ConvertFrom-Json
        $workerFailures=@('COLD_PG_VERSION_INVALID','COLD_OUTPUT_SPACE_INSUFFICIENT','COLD_COUNT_LIMIT','COLD_LINK_OR_SPECIAL_FILE','COLD_EXTENDED_METADATA_UNSUPPORTED','COLD_SOURCE_CHANGED','COLD_BYTE_LIMIT','COLD_FILE_LIMIT','COLD_DEPTH_LIMIT','COLD_TIME_LIMIT','COLD_OUTPUT_NOT_EMPTY','COLD_MODE_INVALID','COLD_OWNER_INVALID')
        if ($workerExit -ne 0) {
            if ($created.status -ceq 'BLOCKED' -and $workerFailures -ccontains $created.failure_category) { throw $created.failure_category }
            throw 'COLD_ARCHIVE_WORKER_FAILED'
        }
        Assert-WorkerReport $created
        Assert-SourceSnapshot $original $helpers $false
        $stage='EXPORT_AND_VERIFY_LOCAL_BACKUP'; Write-Output ('BACKUP_STAGE='+$stage)
        Assert-PrivateBackupDirectory $directory
        if ((Backup-FreeBytes $directory) -lt ($created.archives.database.bytes+$created.archives.runtime.bytes+128MB)) { throw 'COLD_HOST_SPACE_INSUFFICIENT' }
        foreach ($name in @('database.tar','runtime.tar','manifest.json')) {
            $null=Backup-Docker @('cp',($worker[0]+':/output/'+$name),(Join-Path $directory $name)) 0
        }
        Assert-PrivateBackupFiles $directory
        foreach ($name in @('database.tar','runtime.tar','manifest.json')) { Sync-BackupFile (Join-Path $directory $name) }
        foreach ($group in @('database','runtime')) {
            $file=Get-Item -LiteralPath (Join-Path $directory ($group+'.tar'))
            if ($file.Length -ne $created.archives.$group.bytes -or (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant() -cne $created.archives.$group.sha256) { throw 'COLD_LOCAL_ARCHIVE_HASH_MISMATCH' }
        }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $directory 'manifest.json')).Hash.ToLowerInvariant() -cne $created.manifest_sha256) { throw 'COLD_LOCAL_MANIFEST_HASH_MISMATCH' }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $directory 'images.tar')).Hash.ToLowerInvariant() -cne $imageHash) { throw 'COLD_LOCAL_IMAGE_HASH_MISMATCH' }
        Assert-PrivateBackupDirectory $directory; Assert-SourceSnapshot $original $helpers $false
        $receipt=[ordered]@{status='STOPPED_RAW_COPY_VERIFIED_NOT_RECOVERY_PROOF';source_reference=$config.source_reference;manifest_sha256=$created.manifest_sha256;image_archive_sha256=$imageHash;state_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $directory 'BACKUP_STATE.json')).Hash.ToLowerInvariant();archives=$created.archives;file_counts=$created.file_counts;output_volume=$outputVolume;helper_container_ids=$helpers;source_containers_started=$false;source_services_observed_stopped=$true;source_clean_shutdown_verified=$false;account_pause_rechecked=$false;source_data_archive_restore_tested=$false;offline_handoff_performed=$false;automatic_resume=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
        Write-BackupJson (Join-Path $directory 'BACKUP_RECEIPT.json') $receipt
        Assert-PrivateBackupFiles $directory
        $receiptHash=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $directory 'BACKUP_RECEIPT.json')).Hash.ToLowerInvariant()
        [pscustomobject]@{stopped_preservation='RAW_COPY_LOCAL_HASHES_VERIFIED';private_backup_directory=$directory;manifest_sha256=$created.manifest_sha256;receipt_sha256=$receiptHash;source_containers_started=$false;backup_created=$true;clean_shutdown_verified=$false;restore_tested=$false;offline_handoff_performed=$false;installation_ready=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;next_action='KEEP_ORIGINALS_STOPPED_REVIEW_RECEIPT_THEN_ISOLATED_RECOVERY_ON_COPY'} | ConvertTo-Json -Compress | Write-Output
        Write-Output 'STOPPED_RAW_COPY_SAVED_NOT_RECOVERY_VERIFIED_NO_ORDERS'
        return 0
    } catch {
        $reason='PRESERVE_UNAVAILABLE'
        $allowed=@('PRESERVE_SCOPE_CHANGED','PRESERVE_SOURCE_NOT_STOPPED','PRESERVE_EXTERNAL_CONSUMER','PRESERVE_EXPLICIT_MODE_REQUIRED','BACKUP_PLAN_DOCKER_READ_FAILED','BACKUP_PLAN_CONTEXT_INVALID','BACKUP_PLAN_VOLUME_LAYOUT_UNSUPPORTED','BACKUP_PLAN_VOLUME_OPTIONS_REQUIRE_REVIEW','BACKUP_PLAN_TOPOLOGY_JSON_INVALID','BACKUP_PLAN_TOPOLOGY_FIELD_INVALID','BACKUP_PLAN_TOPOLOGY_FIELD_COUNT','COLD_PG_VERSION_INVALID','COLD_OUTPUT_SPACE_INSUFFICIENT','COLD_COUNT_LIMIT','COLD_LINK_OR_SPECIAL_FILE','COLD_EXTENDED_METADATA_UNSUPPORTED','COLD_SOURCE_CHANGED','COLD_BYTE_LIMIT','COLD_FILE_LIMIT','COLD_DEPTH_LIMIT','COLD_TIME_LIMIT','COLD_OUTPUT_NOT_EMPTY','COLD_MODE_INVALID','COLD_OWNER_INVALID','COLD_WINDOWS_ACL_REQUIRED','COLD_LOCALAPPDATA_INVALID','COLD_PRIVATE_STORAGE_UNAVAILABLE','COLD_BACKUP_PATH_REPARSE_POINT','COLD_BACKUP_DIRECTORY_EXISTS','COLD_BACKUP_ACL_INVALID','COLD_CONTAINER_SET_CHANGED','COLD_SOURCE_RUNNING_STATE_CHANGED','COLD_SOURCE_CONFIGURATION_CHANGED','COLD_WORKER_OUTPUT_INVALID','COLD_ANOTHER_BACKUP_ACTIVE','COLD_HOST_SPACE_INSUFFICIENT','COLD_IMAGE_ID_INVALID','COLD_DOCKER_COMMAND_FAILED','COLD_DOCKER_OUTPUT_INVALID','COLD_HELPER_ID_INVALID','COLD_OUTPUT_VOLUME_INVALID','COLD_ARCHIVE_WORKER_FAILED','COLD_LOCAL_ARCHIVE_HASH_MISMATCH','COLD_LOCAL_MANIFEST_HASH_MISMATCH','COLD_LOCAL_IMAGE_HASH_MISMATCH')
        if ($allowed -ccontains $_.Exception.Message) { $reason=$_.Exception.Message }
        if ($null -ne $directory) { try { Write-BackupJson (Join-Path $directory 'FAILURE.json') @{stage=$stage;failure_category=$reason;retained_helper_ids=$helpers;retained_output_volume=$outputVolume;automatic_resume=$false} } catch {} }
        [pscustomobject]@{stopped_preservation='BLOCKED_KEEP_ORIGINALS_STOPPED_NO_RETRY';stage=$stage;failure_category=$reason;private_backup_directory=$directory;images_saved=$imagesSaved;retained_helper_ids=$helpers;retained_output_volume=$outputVolume;source_containers_started=$false;backup_completed=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Depth 8 -Compress | Write-Output
        return 2
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }; if ($null -ne $mutex) { $mutex.Dispose() }
    }
}
