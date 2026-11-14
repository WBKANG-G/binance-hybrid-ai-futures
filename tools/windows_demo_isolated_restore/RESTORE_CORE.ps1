function Restore-CanonicalMetadataJson([object]$Value,[int]$Depth=0) {
    if ($Depth -gt 16) { throw 'RESTORE_METADATA_DEPTH_INVALID' }
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [string] -or $Value -is [ValueType]) { return (ConvertTo-Json -InputObject $Value -Compress) }
    if ($Value -is [System.Array]) {
        $parts=@()
        foreach ($entry in $Value) { $parts+=,(Restore-CanonicalMetadataJson $entry ($Depth+1)) }
        return ('['+($parts -join ',')+']')
    }
    $parts=@()
    foreach ($property in @($Value.PSObject.Properties | Sort-Object -Property Name)) {
        $key=ConvertTo-Json -InputObject ([string]$property.Name) -Compress
        $parts+=,($key+':'+(Restore-CanonicalMetadataJson $property.Value ($Depth+1)))
    }
    return ('{'+($parts -join ',')+'}')
}
function Resolve-RestoreImageMatch([string]$Reference,[string]$Expected) {
    if ($Reference -cnotmatch '^sha256:[0-9a-f]{64}$' -or $Expected -cnotmatch '^sha256:[0-9a-f]{64}$') { return $false }
    $values=@(Backup-Docker @('image','inspect','--format','{{.Id}}',$Reference) 1)
    return ($values.Count -eq 1 -and $values[0] -ceq $Expected)
}
function Restore-SnapshotComparableJson([object]$Item) {
    # Mount list order is not a Docker identity. Preserve EVERY mount field/value.
    $record=[ordered]@{}
    foreach ($property in $Item.PSObject.Properties) {
        if ($property.Name -ceq 'mounts') { $record[$property.Name]=@($Item.mounts | Sort-Object -Property Destination) }
        else { $record[$property.Name]=$property.Value }
    }
    return (Restore-CanonicalMetadataJson ([pscustomobject]$record))
}
function Assert-RestoreSourceSnapshot([object[]]$Original,[string[]]$Helpers,[bool]$Frozen=$false) {
    if ($Frozen) { throw 'RESTORE_SNAPSHOT_MODE_INVALID' }
    $current=@(Container-Ids)
    $expected=@(@($Original | ForEach-Object {$_.id})+$Helpers | Sort-Object)
    if (($current -join '|') -cne ($expected -join '|')) { $script:RestoreScopeProbe=[pscustomobject]@{step='FRESH_CONTAINER_SET';expected_count=$expected.Count;actual_count=$current.Count}; throw 'COLD_CONTAINER_SET_CHANGED' }
    foreach ($old in $Original) {
        $fresh=Read-Container $old.id
        if ($fresh.running -ne $old.running -or $fresh.restarting -or $fresh.paused -or $fresh.dead) { throw 'COLD_SOURCE_RUNNING_STATE_CHANGED' }
        if ((Restore-SnapshotComparableJson $fresh) -cne (Restore-SnapshotComparableJson $old)) {
            $fields=@()
            foreach ($field in @('id','name','image','labels','mounts','running','restarting','paused','dead','restart','networks')) {
                if ($null -eq $old.PSObject.Properties[$field] -or $null -eq $fresh.PSObject.Properties[$field]) { continue }
                $a=$old.$field; $b=$fresh.$field
                if ($field -ceq 'mounts') { $a=@($a | Sort-Object -Property Destination); $b=@($b | Sort-Object -Property Destination) }
                if ((ConvertTo-Json -InputObject $a -Depth 12 -Compress) -cne (ConvertTo-Json -InputObject $b -Depth 12 -Compress)) { $fields+=,$field }
            }
            $script:RestoreScopeProbe=[pscustomobject]@{step='FRESH_SNAPSHOT_MISMATCH';fields=$fields}
            throw 'COLD_SOURCE_CONFIGURATION_CHANGED'
        }
    }
}
function Restore-MountDiagnosticValues([object[]]$Mounts) {
    # Normalize known metadata values ONLY for diagnosis; no authorization uses this.
    $values=@()
    foreach ($mount in @($Mounts | Sort-Object -Property Destination)) {
        $record=[ordered]@{}
        foreach ($field in @('Type','Source','Destination','RW','Name','Driver','Mode','Propagation')) {
            $record[$field]=$null
            if ($null -ne $mount.PSObject.Properties[$field]) { $record[$field]=$mount.$field }
        }
        $values+=,[pscustomobject]$record
    }
    return (ConvertTo-Json -InputObject $values -Depth 8 -Compress)
}
function Assert-RestoredWorkerReport([object]$restored,[object]$receipt) {
    if ((@($restored.PSObject.Properties.Name | Sort-Object) -join '|') -cne (@(@('status','file_counts','pg_uid','pg_gid','clone_pid_removed','source_changed') | Sort-Object) -join '|') -or $restored.status -cne 'CLONE_BYTES_AND_METADATA_RESTORED' -or $restored.source_changed -isnot [bool] -or $restored.source_changed -or $restored.clone_pid_removed -isnot [bool]) { throw 'RESTORE_WORKER_OUTPUT_INVALID' }
    foreach ($name in @('pg_uid','pg_gid')) { $minimum=0; if ($name -ceq 'pg_uid') { $minimum=1 }; if (($restored.$name -isnot [int] -and $restored.$name -isnot [long]) -or $restored.$name -lt $minimum -or $restored.$name -gt 65535) { throw 'RESTORE_WORKER_OUTPUT_INVALID' } }
    if ($restored.file_counts -isnot [pscustomobject] -or (@($restored.file_counts.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'database|runtime') { throw 'RESTORE_WORKER_OUTPUT_INVALID' }
    foreach ($group in @('database','runtime')) { if (($restored.file_counts.$group -isnot [int] -and $restored.file_counts.$group -isnot [long]) -or $restored.file_counts.$group -lt 1 -or $restored.file_counts.$group -gt 50000 -or $restored.file_counts.$group -ne $receipt.file_counts.$group) { throw 'RESTORE_WORKER_OUTPUT_INVALID' } }
}
# Only freshly created clone IDs may be started or signalled. No source-volume mounts.
function Restore-Hash([string]$Path) { return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }
function Read-RestoreJson([string]$Path,[long]$Maximum=1048576) {
    $file=Get-Item -LiteralPath $Path
    if ($file.Length -lt 2 -or $file.Length -gt $Maximum -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'RESTORE_INPUT_INVALID' }
    return (Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json)
}
function Find-RetainedBackup([object]$Config) {
    $base=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
    $folders=@(Get-ChildItem -LiteralPath $base -Directory -Filter 'cold-20261114-*')
    if ($folders.Count -gt 32) { throw 'RESTORE_BACKUP_SEARCH_LIMIT' }
    $matches=@()
    foreach ($folder in $folders) {
        if (($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'RESTORE_INPUT_INVALID' }
        $receipt=Join-Path $folder.FullName 'BACKUP_RECEIPT.json'
        if ([IO.File]::Exists($receipt)) {
            Assert-PrivateBackupDirectory $folder.FullName
            Assert-PrivateBackupFiles $folder.FullName
            if ((Restore-Hash $receipt) -ceq $Config.receipt_sha256) { $matches+=,$folder.FullName }
        }
    }
    if ($matches.Count -ne 1) { throw 'RESTORE_RETAINED_RECEIPT_NOT_UNIQUE' }
    return $matches[0]
}
function Assert-InputHashes([string]$Directory,[object]$Config,[object]$Receipt) {
    Assert-PrivateBackupDirectory $Directory; Assert-PrivateBackupFiles $Directory
    if ((Restore-Hash (Join-Path $Directory 'BACKUP_RECEIPT.json')) -cne $Config.receipt_sha256 -or (Restore-Hash (Join-Path $Directory 'manifest.json')) -cne $Config.manifest_sha256 -or $Receipt.manifest_sha256 -cne $Config.manifest_sha256) { throw 'RESTORE_RETAINED_HASH_MISMATCH' }
    if ($Receipt.status -cne 'STOPPED_RAW_COPY_VERIFIED_NOT_RECOVERY_PROOF' -or $Receipt.source_reference -cne $Config.preservation_source_reference) { throw 'RESTORE_RECEIPT_INVALID' }
    foreach ($flag in @('source_containers_started','source_clean_shutdown_verified','account_pause_rechecked','source_data_archive_restore_tested','offline_handoff_performed','automatic_resume','automatic_retry','execution_approved','orders_enabled','live_trading_enabled')) {
        if ($Receipt.$flag -isnot [bool] -or $Receipt.$flag) { throw 'RESTORE_RECEIPT_INVALID' }
    }
    if ($Receipt.source_services_observed_stopped -isnot [bool] -or -not $Receipt.source_services_observed_stopped -or $Receipt.order_writes -ne 0 -or $Receipt.actual_http_attempts -ne 0) { throw 'RESTORE_RECEIPT_INVALID' }
    foreach ($pair in @(@('BACKUP_STATE.json',$Receipt.state_sha256),@('images.tar',$Receipt.image_archive_sha256))) {
        if ($pair[1] -isnot [string] -or $pair[1] -cnotmatch '^[0-9a-f]{64}$' -or (Restore-Hash (Join-Path $Directory $pair[0])) -cne $pair[1]) { throw 'RESTORE_INPUT_HASH_MISMATCH' }
    }
    foreach ($group in @('database','runtime')) {
        $record=$Receipt.archives.$group
        $file=Get-Item -LiteralPath (Join-Path $Directory ($group+'.tar'))
        if ($record.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $file.Length -ne $record.bytes -or (Restore-Hash $file.FullName) -cne $record.sha256) { throw 'RESTORE_INPUT_HASH_MISMATCH' }
    }
}
function Read-RestoreSources([object]$Config,[object]$Receipt,[object]$State) {
    $script:RestoreScopeProbe=[pscustomobject]@{step='STATE_HEADER'}
    if ($State.protocol -cne 'STOPPED_UNCLEAN_PRESERVATION_V1' -or $State.source_reference -cne $Config.preservation_source_reference -or $State.pgdata_relative -cne '18/docker' -or $State.key_session_excluded -isnot [bool] -or -not $State.key_session_excluded -or $State.automatic_resume_authorized -isnot [bool] -or $State.automatic_resume_authorized -or @($State.canonical_images).Count -ne 2) { throw 'RESTORE_STATE_INVALID' }
    foreach ($name in @($State.database_volume,$State.runtime_volume,$Receipt.output_volume)) { if ($name -isnot [string] -or $name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$') { throw 'RESTORE_STATE_INVALID' } }
    if ($State.database_volume -ceq $State.runtime_volume -or @($State.database_volume,$State.runtime_volume) -ccontains $Receipt.output_volume) { throw 'RESTORE_STATE_INVALID' }
    foreach ($id in $State.canonical_images) { if ($id -isnot [string] -or $id -cnotmatch '^sha256:[0-9a-f]{64}$') { throw 'RESTORE_STATE_INVALID' } }
    if (@($Receipt.helper_container_ids).Count -ne 1 -or $Receipt.helper_container_ids[0] -cnotmatch '^[0-9a-f]{64}$') { throw 'RESTORE_STATE_INVALID' }
    $items=@(@(Container-Ids) | ForEach-Object {Read-Container $_})
    $script:RestoreScopeProbe=[pscustomobject]@{step='PRESERVATION_HELPER'}
    $oldWorker=@($items | Where-Object {$_.id -ceq $Receipt.helper_container_ids[0]})
    if ($oldWorker.Count -ne 1 -or $oldWorker[0].running -or $oldWorker[0].paused -or $oldWorker[0].restarting -or $oldWorker[0].dead -or $oldWorker[0].restart.Name -cne 'no' -or $oldWorker[0].restart.MaximumRetryCount -ne 0 -or -not (Resolve-RestoreImageMatch $oldWorker[0].image $State.canonical_images[0]) -or @($oldWorker[0].mounts).Count -ne 3) { throw 'RESTORE_PRESERVATION_HELPER_CHANGED' }
    if (@($State.source_snapshots | ForEach-Object {$_.id}) -ccontains $oldWorker[0].id -or @($Config.api_container_id,$Config.database_container_id) -ccontains $oldWorker[0].id) { throw 'RESTORE_PRESERVATION_HELPER_CHANGED' }
    foreach ($spec in @(@('/source/database',$State.database_volume,$false),@('/source/runtime',$State.runtime_volume,$false),@('/output',$Receipt.output_volume,$true))) {
        $mount=@($oldWorker[0].mounts | Where-Object {$_.Destination -ceq $spec[0]})
        if ($mount.Count -ne 1 -or $mount[0].Type -cne 'volume' -or $mount[0].Name -cne $spec[1] -or $mount[0].RW -ne $spec[2]) { throw 'RESTORE_PRESERVATION_HELPER_CHANGED' }
    }
    $labelled=@($items | Where-Object {(Project $_) -ceq 'hybrid-windows'})
    # Only this verified, receipt-bound reader is excluded, never a label/name pattern.
    $project=@($labelled | Where-Object {$_.id -cne $oldWorker[0].id})
    $script:RestoreScopeProbe=[pscustomobject]@{step='CONTAINER_COUNTS';labelled_project_count=$labelled.Count;project_count=$project.Count;saved_count=@($State.source_snapshots).Count;verified_helper_excluded=$true;helper_had_project_label=((Project $oldWorker[0]) -ceq 'hybrid-windows')}
    if ($project.Count -ne 7 -or @($State.source_snapshots).Count -ne 6) { throw 'RESTORE_SOURCE_SCOPE_CHANGED' }
    foreach ($role in @('api','db','web','alerts','key-session')) {
        $expected=1; if ($role -ceq 'api') { $expected=3 }
        $actual=@($project | Where-Object {(Service $_) -ceq $role}).Count
        $script:RestoreScopeProbe=[pscustomobject]@{step='ROLE_COUNT';role=$role;expected=$expected;actual=$actual}
        if ($actual -ne $expected) { throw 'RESTORE_SOURCE_SCOPE_CHANGED' }
    }
    $script:RestoreScopeProbe=[pscustomobject]@{step='STOPPED_STATE'}
    foreach ($item in $project) {
        if ($item.running -or $item.paused -or $item.dead -or $item.restarting -or $item.restart.Name -cne 'no' -or $item.restart.MaximumRetryCount -ne 0) { throw 'RESTORE_ORIGINAL_NOT_STOPPED' }
    }
    $api=@($project | Where-Object {$_.id -ceq $Config.api_container_id -and (Service $_) -ceq 'api' -and $_.name -ceq '/hybrid-windows-api-1'})
    $db=@($project | Where-Object {$_.id -ceq $Config.database_container_id -and (Service $_) -ceq 'db'})
    $script:RestoreScopeProbe=[pscustomobject]@{step='PRIMARY_SELECTION';api_matches=$api.Count;database_matches=$db.Count}
    if ($api.Count -ne 1 -or $db.Count -ne 1) { throw 'RESTORE_SOURCE_SCOPE_CHANGED' }
    $database=Volume-Mount $db[0] '/var/lib/postgresql'; $runtime=Volume-Mount $api[0] '/app/demo-export-state'
    $script:RestoreScopeProbe=[pscustomobject]@{step='PRIMARY_BINDINGS';database_volume_match=($database.Name -ceq $State.database_volume);runtime_volume_match=($runtime.Name -ceq $State.runtime_volume);api_image_reference_match=($api[0].image -ceq $State.canonical_images[0]);database_image_reference_match=($db[0].image -ceq $State.canonical_images[1]);api_image_resolves_to_canonical=$null;database_image_resolves_to_canonical=$null}
    $script:RestoreScopeProbe.api_image_resolves_to_canonical=(Resolve-RestoreImageMatch $api[0].image $State.canonical_images[0])
    $script:RestoreScopeProbe.database_image_resolves_to_canonical=(Resolve-RestoreImageMatch $db[0].image $State.canonical_images[1])
    if ($database.Name -cne $State.database_volume -or $runtime.Name -cne $State.runtime_volume -or -not $script:RestoreScopeProbe.api_image_resolves_to_canonical -or -not $script:RestoreScopeProbe.database_image_resolves_to_canonical) { throw 'RESTORE_SOURCE_SCOPE_CHANGED' }
    $snapshotIndex=-1
    foreach ($saved in $State.source_snapshots) {
        $snapshotIndex++
        $now=@($project | Where-Object {$_.id -ceq $saved.id -and (Service $_) -cne 'key-session'})
        $safeRole='OTHER'
        if (@('api','db','alerts','web') -ccontains $saved.service) { $safeRole=$saved.service }
        $script:RestoreScopeProbe=[pscustomobject]@{step='SAVED_SNAPSHOT';snapshot_index=$snapshotIndex;role=$safeRole;container_matches=$now.Count;image_match=$null;service_match=$null;restart_json_match=$null;restart_values_match=$null;mounts_json_match=$null;mounts_values_match=$null;restart_metadata_match=$null;mounts_metadata_match=$null}
        if ($now.Count -eq 1) {
            $script:RestoreScopeProbe.image_match=($now[0].image -ceq $saved.image)
            $script:RestoreScopeProbe.service_match=((Service $now[0]) -ceq $saved.service)
            $script:RestoreScopeProbe.restart_json_match=(($now[0].restart | ConvertTo-Json -Compress) -ceq ($saved.restart | ConvertTo-Json -Compress))
            $script:RestoreScopeProbe.restart_values_match=($now[0].restart.Name -ceq $saved.restart.Name -and $now[0].restart.MaximumRetryCount -eq $saved.restart.MaximumRetryCount)
            $currentMounts=@($now[0].mounts | Where-Object {$_.Destination -cne '/run/demo-keys'})
            $script:RestoreScopeProbe.mounts_json_match=(($currentMounts | ConvertTo-Json -Depth 8 -Compress) -ceq (@($saved.mounts) | ConvertTo-Json -Depth 8 -Compress))
            $script:RestoreScopeProbe.mounts_values_match=((Restore-MountDiagnosticValues $currentMounts) -ceq (Restore-MountDiagnosticValues @($saved.mounts)))
            $script:RestoreScopeProbe.restart_metadata_match=((Restore-CanonicalMetadataJson $now[0].restart) -ceq (Restore-CanonicalMetadataJson $saved.restart))
            $script:RestoreScopeProbe.mounts_metadata_match=((Restore-CanonicalMetadataJson @($currentMounts | Sort-Object -Property Destination)) -ceq (Restore-CanonicalMetadataJson @($saved.mounts | Sort-Object -Property Destination)))
        }
        if ($now.Count -ne 1 -or -not $script:RestoreScopeProbe.image_match -or -not $script:RestoreScopeProbe.service_match -or -not $script:RestoreScopeProbe.restart_metadata_match -or -not $script:RestoreScopeProbe.mounts_metadata_match) { throw 'RESTORE_SOURCE_SCOPE_CHANGED' }
    }
    $script:RestoreScopeProbe=[pscustomobject]@{step='EXTERNAL_CONSUMERS'}
    foreach ($item in $items) {
        if ((Project $item) -cne 'hybrid-windows' -and $item.id -cne $Receipt.helper_container_ids[0] -and ((Uses-Source $item $database) -or (Uses-Source $item $runtime))) { throw 'RESTORE_EXTERNAL_SOURCE_CONSUMER' }
    }
    $script:RestoreScopeProbe=[pscustomobject]@{step='FINAL_SNAPSHOT'}
    Assert-RestoreSourceSnapshot $items @() $false
    $script:RestoreScopeProbe=[pscustomobject]@{step='COMPLETE';source_project_count=$project.Count;labelled_project_count=$labelled.Count;verified_helper_excluded=$true;helper_had_project_label=((Project $oldWorker[0]) -ceq 'hybrid-windows')}
    return ,$items
}
function Assert-NewClone([string]$Id,[string]$Volume,[object[]]$Original) {
    if (@($Original | ForEach-Object {$_.id}) -ccontains $Id) { throw 'RESTORE_CLONE_ID_INVALID' }
    $item=Read-Container $Id
    if ($item.restart.Name -cne 'no' -or @($item.mounts).Count -ne 1 -or $item.mounts[0].Type -cne 'volume' -or $item.mounts[0].Name -cne $Volume -or $item.mounts[0].Destination -cne '/var/lib/postgresql') { throw 'RESTORE_CLONE_MOUNT_INVALID' }
    $network=@(Backup-Docker @('inspect','--format','{{.HostConfig.NetworkMode}}',$Id) 1)
    if ($network.Count -ne 1 -or $network[0] -cne 'none') { throw 'RESTORE_CLONE_NETWORK_INVALID' }
}
function Stop-OwnedClone([string]$Id,[string]$Volume,[object[]]$Original) {
    Assert-NewClone $Id $Volume $Original
    $running=@(Backup-Docker @('inspect','--format','{{.State.Running}}',$Id) 1)
    if ($running.Count -eq 1 -and $running[0] -ceq 'false') { return }
    if ($running.Count -ne 1 -or $running[0] -cne 'true') { throw 'RESTORE_CLONE_STATE_INVALID' }
    $null=Backup-Docker @('kill','--signal=SIGINT',$Id) 1
    $watch=[Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt 30) {
        $running=@(Backup-Docker @('inspect','--format','{{.State.Running}}',$Id) 1)
        if ($running.Count -eq 1 -and $running[0] -ceq 'false') { return }
        Start-Sleep -Milliseconds 500
    }
    throw 'RESTORE_CLONE_STOP_TIMEOUT'
}
function Invoke-IsolatedRestore([string]$Root,[switch]$ExecuteRestore,[switch]$CheckOnly) {
    $script:RestoreScopeProbe=$null
    $stage='OPT_IN'; $handles=@(); $helpers=@(); $volumes=@(); $clone=$null; $original=@(); $directory=$null; $mutex=$null; $locked=$false; $cloneStopped=$false
    try {
        if (($ExecuteRestore -and $CheckOnly) -or (-not $ExecuteRestore -and -not $CheckOnly)) { throw 'RESTORE_EXPLICIT_MODE_REQUIRED' }
        $config=Read-RestoreJson (Join-Path $Root 'CONFIG.json')
        foreach ($name in @('manifest_sha256','receipt_sha256')) { if ($config.$name -cnotmatch '^[0-9a-f]{64}$') { throw 'RESTORE_CONFIG_INVALID' } }
        $stage='PINNED_RECEIPT_AND_FILE_HASHES'; Write-Output ('RESTORE_STAGE='+$stage)
        $backup=Find-RetainedBackup $config
        # Keep all six input files open with read sharing only until final verification.
        foreach ($name in @('BACKUP_RECEIPT.json','BACKUP_STATE.json','manifest.json','database.tar','runtime.tar','images.tar')) {
            $handles+=,(New-Object IO.FileStream((Join-Path $backup $name),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read))
        }
        if ((Restore-Hash (Join-Path $backup 'BACKUP_RECEIPT.json')) -cne $config.receipt_sha256) { throw 'RESTORE_RETAINED_HASH_MISMATCH' }
        $receipt=Read-RestoreJson (Join-Path $backup 'BACKUP_RECEIPT.json')
        Assert-InputHashes $backup $config $receipt
        $state=Read-RestoreJson (Join-Path $backup 'BACKUP_STATE.json')
        $stage='ORIGINALS_STOPPED_AND_SCOPE_UNCHANGED'
        $original=Read-RestoreSources $config $receipt $state
        foreach ($id in $state.canonical_images) {
            $image=@(Backup-Docker @('image','inspect','--format','{{.Id}}',$id) 1)
            if ($image.Count -ne 1 -or $image[0] -cne $id) { throw 'RESTORE_CACHED_IMAGE_UNAVAILABLE' }
        }
        if ($CheckOnly) {
            [pscustomobject]@{isolated_restore='INPUT_HASHES_AND_STOPPED_SCOPE_VERIFIED';scope_probe=$script:RestoreScopeProbe;clone_created=$false;restore_tested=$false;originals_started=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
            Write-Output 'RESTORE_CHECK_PASSED_NO_CLONE_NO_SOURCE_START_NO_ORDERS'
            return 0
        }
        $stage='PRIVATE_RESTORE_REPORT_STORAGE'
        $mutex=New-Object Threading.Mutex($false,'Global\WBKANG_DEMO_COLD_BACKUP')
        $locked=$mutex.WaitOne(0)
        if (-not $locked) { throw 'COLD_ANOTHER_BACKUP_ACTIVE' }
        $token=[Guid]::NewGuid().ToString('N')
        $directory=New-PrivateBackupDirectory $token
        $stage='CREATE_FRESH_CLONE_VOLUMES'; Write-Output ('RESTORE_STAGE='+$stage)
        foreach ($group in @('database','runtime')) {
            $name='demo-restore-'+$group+'-'+$token
            $created=@(Backup-Docker @('volume','create',$name) 1)
            if ($created.Count -ne 1 -or $created[0] -cne $name) { throw 'RESTORE_NEW_VOLUME_INVALID' }
            $volumes+=,$name
        }
        if ($backup.Contains(',') -or $backup.Contains('"')) { throw 'RESTORE_BIND_PATH_UNSUPPORTED' }
        $worker=@(Backup-Docker @('create','--pull=never','--interactive','--network','none','--restart','no','--read-only','--memory','512m','--pids-limit','32','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--cap-add','CHOWN','--cap-add','FOWNER','--cap-add','FSETID','--security-opt','no-new-privileges','--user','0:0','--mount',('type=bind,source='+$backup+',target=/bundle,readonly'),'--mount',('type=volume,source='+$volumes[0]+',target=/clone/database,volume-nocopy'),'--mount',('type=volume,source='+$volumes[1]+',target=/clone/runtime,volume-nocopy'),'--entrypoint','python',$state.canonical_images[0],'-I','-B','-') 1)
        if ($worker.Count -ne 1 -or $worker[0] -cnotmatch '^[0-9a-f]{64}$' -or @($original | ForEach-Object {$_.id}) -ccontains $worker[0]) { throw 'RESTORE_WORKER_ID_INVALID' }
        $helpers+=,$worker[0]
        Write-BackupJson (Join-Path $directory 'RESTORE_RESOURCES.json') @{backup_receipt_sha256=$config.receipt_sha256;manifest_sha256=$config.manifest_sha256;owned_helper_ids=$helpers;clone_volumes=$volumes;automatic_resume=$false}
        Assert-PrivateBackupFiles $directory
        Assert-RestoreSourceSnapshot $original $helpers $false
        $stage='RESTORE_BYTES_AND_METADATA_TO_COPY'; Write-Output ('RESTORE_STAGE='+$stage)
        $archive=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'cold_archive.py')))
        $driver=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'restore_worker.py')))
        $encoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((@{manifest_sha256=$config.manifest_sha256} | ConvertTo-Json -Compress)))
        $payload="import base64,json`nARCHIVE_SOURCE=base64.b64decode('$archive')`nCONFIG=json.loads(base64.b64decode('$encoded'))`nexec(compile(base64.b64decode('$driver'),'<isolated-restore>','exec'))"
        $lines=@($payload | & docker start --attach --interactive $worker[0] 2>$null)
        if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 2048) { throw 'RESTORE_WORKER_BLOCKED' }
        $restored=$lines[0] | ConvertFrom-Json
        Assert-RestoredWorkerReport $restored $receipt
        Assert-RestoreSourceSnapshot $original $helpers $false
        $stage='START_NETWORKLESS_COPY_ONLY'; Write-Output ('RESTORE_STAGE='+$stage)
        $arguments=@('create','--pull=never','--network','none','--restart','no','--read-only','--memory','1g','--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777','--user',([string]$restored.pg_uid+':'+[string]$restored.pg_gid),'--mount',('type=volume,source='+$volumes[0]+',target=/var/lib/postgresql,volume-nocopy'),'--entrypoint','postgres',$state.canonical_images[1],'-D','/var/lib/postgresql/18/docker')
        foreach ($setting in @('listen_addresses=','unix_socket_directories=/tmp','default_transaction_read_only=on','autovacuum=off','ssl=off','archive_mode=off','shared_preload_libraries=','session_preload_libraries=','local_preload_libraries=','max_worker_processes=0','primary_conninfo=','primary_slot_name=','restore_command=','archive_command=','archive_cleanup_command=','recovery_end_command=','logging_collector=off','log_destination=stderr')) { $arguments+=@('-c',$setting) }
        $created=@(Backup-Docker $arguments 1)
        if ($created.Count -ne 1 -or $created[0] -cnotmatch '^[0-9a-f]{64}$') { throw 'RESTORE_CLONE_ID_INVALID' }
        $clone=$created[0]; $helpers+=,$clone
        Assert-NewClone $clone $volumes[0] $original
        Write-BackupJson (Join-Path $directory 'CLONE_CREATED.json') @{clone_container_id=$clone;clone_volumes=$volumes;original_services_started=$false}
        $null=Backup-Docker @('start',$clone) 1
        $watch=[Diagnostics.Stopwatch]::StartNew(); $ready=$false
        while ($watch.Elapsed.TotalSeconds -lt 60) {
            $null=@(& docker exec $clone pg_isready -h /tmp -U dev -d hybrid -t 1 2>$null)
            if ($LASTEXITCODE -eq 0) { $ready=$true; break }
            $running=@(Backup-Docker @('inspect','--format','{{.State.Running}}',$clone) 1)
            if ($running.Count -ne 1 -or $running[0] -cne 'true') { throw 'RESTORE_CLONE_START_FAILED' }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw 'RESTORE_CLONE_READY_TIMEOUT' }
        $stage='READ_ONLY_ACCOUNT_ON_COPY'; Write-Output ('RESTORE_STAGE='+$stage)
        $sql=[IO.File]::ReadAllText((Join-Path $Root 'account_check.sql'))
        $observed=@()
        for ($iteration=0;$iteration -lt 2;$iteration++) {
            $lines=@($sql | & docker exec -i --env PGCONNECT_TIMEOUT=3 $clone psql -X -q -t -A --no-password -v ON_ERROR_STOP=1 -h /tmp -U dev -d hybrid 2>$null)
            if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 2048) { throw 'RESTORE_CLONE_READ_FAILED' }
            $account=$lines[0] | ConvertFrom-Json
            if ((@($account.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'account_state_sha256|daily_key|orders|paused|transaction_read_only|unresolved_requests|weekly_key' -or $account.paused -isnot [bool] -or -not $account.paused -or $account.transaction_read_only -cne 'on' -or $account.orders -ne 34 -or $account.unresolved_requests -ne 4 -or $account.daily_key -cne '2026-11-13' -or $account.weekly_key -cne '2026-11-09' -or $account.account_state_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'RESTORE_CLONE_ACCOUNT_CHECK_FAILED' }
            $observed+=,($account | ConvertTo-Json -Compress)
        }
        if ($observed[0] -cne $observed[1]) { throw 'RESTORE_CLONE_ACCOUNT_CHANGED' }
        $stage='STOP_COPY_AND_REVERIFY_INPUTS'; Write-Output ('RESTORE_STAGE='+$stage)
        Stop-OwnedClone $clone $volumes[0] $original; $cloneStopped=$true
        $exit=@(Backup-Docker @('inspect','--format','{{.State.ExitCode}} {{.State.OOMKilled}}',$clone) 1)
        if ($exit.Count -ne 1 -or $exit[0] -cne '0 false') { throw 'RESTORE_CLONE_NOT_CLEANLY_STOPPED' }
        Assert-RestoreSourceSnapshot $original $helpers $false
        Assert-InputHashes $backup $config $receipt
        $result=[ordered]@{isolated_restore='COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED';source_reference=$config.source_reference;backup_manifest_sha256=$config.manifest_sha256;backup_receipt_sha256=$config.receipt_sha256;original_services_started=$false;original_volume_mounted_in_restore_helpers=$false;input_hashes_reverified=$true;archive_bytes_and_metadata_verified_before_clone_recovery=$true;clone_pid_removed=$restored.clone_pid_removed;clone_container_id=$clone;retained_helper_ids=$helpers;retained_clone_volumes=$volumes;clone_network='none';clone_stopped=$true;clone_clean_exit_verified=$true;account=$account;financial_write_attempted=$false;full_financial_proof_verified=$false;offline_handoff_performed=$false;installation_ready=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
        Write-BackupJson (Join-Path $directory 'ISOLATED_RESTORE_RECEIPT.json') $result
        Assert-PrivateBackupFiles $directory
        [pscustomobject]@{isolated_restore='LOCAL_COPY_RECOVERY_VERIFIED_ORIGINALS_STOPPED';private_restore_report_directory=$directory;restore_receipt_sha256=(Restore-Hash (Join-Path $directory 'ISOLATED_RESTORE_RECEIPT.json'));backup_hashes_unchanged=$true;clone_stopped=$true;originals_started=$false;restored_account_paused=$true;orders_preserved=34;unresolved_requests_preserved=4;stored_day_preserved='2026-11-13';offline_handoff_performed=$false;installation_ready=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;next_action='KEEP_ORIGINALS_AND_COPY_STOPPED_REVIEW_RECEIPT_BEFORE_OFFLINE_HANDOFF'} | ConvertTo-Json -Compress | Write-Output
        Write-Output 'ISOLATED_COPY_RECOVERED_READ_CHECKED_STOPPED_NO_ORDERS'
        return 0
    } catch {
        $reason='RESTORE_UNAVAILABLE'
        $allowed=@('RESTORE_EXPLICIT_MODE_REQUIRED','RESTORE_CONFIG_INVALID','RESTORE_BACKUP_SEARCH_LIMIT','RESTORE_INPUT_INVALID','RESTORE_RETAINED_RECEIPT_NOT_UNIQUE','RESTORE_RETAINED_HASH_MISMATCH','RESTORE_RECEIPT_INVALID','RESTORE_INPUT_HASH_MISMATCH','RESTORE_STATE_INVALID','RESTORE_SOURCE_SCOPE_CHANGED','RESTORE_ORIGINAL_NOT_STOPPED','RESTORE_PRESERVATION_HELPER_CHANGED','RESTORE_EXTERNAL_SOURCE_CONSUMER','RESTORE_CLONE_ID_INVALID','RESTORE_CLONE_MOUNT_INVALID','RESTORE_CLONE_NETWORK_INVALID','RESTORE_CLONE_STATE_INVALID','RESTORE_CLONE_STOP_TIMEOUT','RESTORE_CACHED_IMAGE_UNAVAILABLE','RESTORE_NEW_VOLUME_INVALID','RESTORE_BIND_PATH_UNSUPPORTED','RESTORE_WORKER_ID_INVALID','RESTORE_WORKER_BLOCKED','RESTORE_WORKER_OUTPUT_INVALID','RESTORE_CLONE_START_FAILED','RESTORE_CLONE_READY_TIMEOUT','RESTORE_CLONE_READ_FAILED','RESTORE_CLONE_ACCOUNT_CHECK_FAILED','RESTORE_CLONE_ACCOUNT_CHANGED','RESTORE_CLONE_NOT_CLEANLY_STOPPED','COLD_ANOTHER_BACKUP_ACTIVE','COLD_WINDOWS_ACL_REQUIRED','COLD_BACKUP_ACL_INVALID','COLD_DOCKER_COMMAND_FAILED','COLD_DOCKER_OUTPUT_INVALID','COLD_CONTAINER_SET_CHANGED','COLD_SOURCE_RUNNING_STATE_CHANGED','COLD_SOURCE_CONFIGURATION_CHANGED','BACKUP_PLAN_DOCKER_READ_FAILED')
        if ($allowed -ccontains $_.Exception.Message) { $reason=$_.Exception.Message }
        if ($null -ne $clone -and -not $cloneStopped -and $volumes.Count -eq 2) { try { Stop-OwnedClone $clone $volumes[0] $original; $cloneStopped=$true } catch {} }
        if ($null -ne $directory) { try { Write-BackupJson (Join-Path $directory 'FAILURE.json') @{stage=$stage;failure_category=$reason;retained_helper_ids=$helpers;retained_clone_volumes=$volumes;clone_stopped=$cloneStopped;automatic_retry=$false} } catch {} }
        [pscustomobject]@{isolated_restore='BLOCKED_KEEP_ORIGINALS_STOPPED_NO_RETRY';stage=$stage;failure_category=$reason;scope_probe=$script:RestoreScopeProbe;retained_helper_ids=$helpers;retained_clone_volumes=$volumes;clone_stopped=$cloneStopped;originals_started=$false;restore_tested=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Depth 5 -Compress | Write-Output
        return 2
    } finally {
        foreach ($handle in $handles) { $handle.Dispose() }
        if ($locked) { $mutex.ReleaseMutex() }; if ($null -ne $mutex) { $mutex.Dispose() }
    }
}
