# Only file reads, new destination files, and local Docker metadata queries.
function Find-RestoreEvidence([object]$Config) {
    $base=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
    $found=@(); $folders=@(Get-ChildItem -LiteralPath $base -Directory -Filter 'cold-20261114-*')
    if ($folders.Count -gt 32) { throw 'HANDOFF_SEARCH_LIMIT' }
    foreach ($folder in $folders) {
        if (($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'HANDOFF_REPARSE_POINT' }
        $path=Join-Path $folder.FullName 'ISOLATED_RESTORE_RECEIPT.json'
        if ([IO.File]::Exists($path)) {
            Assert-PrivateBackupDirectory $folder.FullName; Assert-PrivateBackupFiles $folder.FullName
            if ((Restore-Hash $path) -ceq $Config.restore_receipt_sha256) { $found+=,$path }
        }
    }
    if ($found.Count -ne 1) { throw 'HANDOFF_RESTORE_RECEIPT_NOT_UNIQUE' }
    return $found[0]
}
function Assert-RestoreEvidence([object]$Value,[object]$Config) {
    if ($Value.isolated_restore -cne 'COPY_RECOVERED_AND_ACCOUNT_READS_VERIFIED' -or $Value.source_reference -cne $Config.restore_source_reference -or $Value.backup_manifest_sha256 -cne $Config.manifest_sha256 -or $Value.backup_receipt_sha256 -cne $Config.receipt_sha256 -or $Value.clone_network -cne 'none') { throw 'HANDOFF_RESTORE_RECEIPT_INVALID' }
    foreach ($flag in @('input_hashes_reverified','archive_bytes_and_metadata_verified_before_clone_recovery','clone_stopped','clone_clean_exit_verified')) { if ($Value.$flag -isnot [bool] -or -not $Value.$flag) { throw 'HANDOFF_RESTORE_RECEIPT_INVALID' } }
    foreach ($flag in @('original_services_started','original_volume_mounted_in_restore_helpers','financial_write_attempted','full_financial_proof_verified','offline_handoff_performed','installation_ready','automatic_retry','execution_approved','orders_enabled','live_trading_enabled')) { if ($Value.$flag -isnot [bool] -or $Value.$flag) { throw 'HANDOFF_RESTORE_RECEIPT_INVALID' } }
    if ($Value.order_writes -ne 0 -or $Value.actual_http_attempts -ne 0 -or $Value.account.paused -isnot [bool] -or -not $Value.account.paused -or $Value.account.orders -ne 34 -or $Value.account.unresolved_requests -ne 4 -or $Value.account.daily_key -cne '2026-11-13' -or $Value.account.weekly_key -cne '2026-11-09' -or $Value.account.transaction_read_only -cne 'on' -or $Value.account.account_state_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'HANDOFF_RESTORE_RECEIPT_INVALID' }
    if (@($Value.retained_helper_ids).Count -ne 2 -or @($Value.retained_clone_volumes).Count -ne 2 -or $Value.clone_container_id -cne $Value.retained_helper_ids[1]) { throw 'HANDOFF_RESTORE_RECEIPT_INVALID' }
    foreach ($id in $Value.retained_helper_ids) { if ($id -cnotmatch '^[0-9a-f]{64}$') { throw 'HANDOFF_RESTORE_RECEIPT_INVALID' } }
    foreach ($name in $Value.retained_clone_volumes) { if ($name -cnotmatch '^demo-restore-(database|runtime)-[0-9a-f]{32}$') { throw 'HANDOFF_RESTORE_RECEIPT_INVALID' } }
}
function Assert-HandoffStoppedScope([object]$Config,[object]$Backup,[object]$State,[object]$Recovery) {
    if ($State.protocol -cne 'STOPPED_UNCLEAN_PRESERVATION_V1' -or $State.source_reference -cne $Config.preservation_source_reference -or @($State.canonical_images).Count -ne 2 -or @(@($State.database_volume,$State.runtime_volume,$Backup.output_volume)+@($Recovery.retained_clone_volumes) | Select-Object -Unique).Count -ne 5) { throw 'HANDOFF_SCOPE_INVALID' }
    $items=@(@(Container-Ids) | ForEach-Object {Read-Container $_})
    $sourceIds=@($State.source_snapshots | ForEach-Object {$_.id})
    $helperIds=@($Backup.helper_container_ids)+@($Recovery.retained_helper_ids)
    if ($sourceIds.Count -ne 6 -or $helperIds.Count -ne 3 -or @(@($sourceIds+$helperIds) | Select-Object -Unique).Count -ne 9) { throw 'HANDOFF_SCOPE_INVALID' }
    $keys=@($items | Where-Object {(Project $_) -ceq 'hybrid-windows' -and (Service $_) -ceq 'key-session' -and $helperIds -cnotcontains $_.id})
    if ($keys.Count -ne 1) { throw 'HANDOFF_SCOPE_INVALID' }
    $expected=@($sourceIds+$helperIds+@($keys[0].id))
    foreach ($item in $items) {
        if ((Project $item) -ceq 'hybrid-windows' -and $expected -cnotcontains $item.id) { throw 'HANDOFF_UNREVIEWED_PROJECT_CONTAINER' }
    }
    foreach ($id in $expected) {
        $selected=@($items | Where-Object {$_.id -ceq $id})
        if ($selected.Count -ne 1 -or $selected[0].running -or $selected[0].paused -or $selected[0].dead -or $selected[0].restarting -or $selected[0].restart.Name -cne 'no' -or $selected[0].restart.MaximumRetryCount -ne 0) { throw 'HANDOFF_CONTAINER_NOT_STOPPED' }
    }
    foreach ($saved in $State.source_snapshots) {
        $now=@($items | Where-Object {$_.id -ceq $saved.id})[0]
        if ((Project $now) -cne 'hybrid-windows' -or (Service $now) -cne $saved.service -or $now.image -cne $saved.image -or (Restore-CanonicalMetadataJson $now.restart) -cne (Restore-CanonicalMetadataJson $saved.restart) -or (Restore-CanonicalMetadataJson @($now.mounts | Where-Object {$_.Destination -cne '/run/demo-keys'} | Sort-Object Destination)) -cne (Restore-CanonicalMetadataJson @($saved.mounts | Sort-Object Destination))) { throw 'HANDOFF_ORIGINAL_CONFIGURATION_CHANGED' }
    }
    $api=@($items | Where-Object {$_.id -ceq $Config.api_container_id})[0]; $db=@($items | Where-Object {$_.id -ceq $Config.database_container_id})[0]
    $runtime=Volume-Mount $api '/app/demo-export-state'; $database=Volume-Mount $db '/var/lib/postgresql'
    if ($runtime.Name -cne $State.runtime_volume -or $database.Name -cne $State.database_volume) { throw 'HANDOFF_SCOPE_INVALID' }
    foreach ($item in $items) {
        if ($expected -cnotcontains $item.id -and ((Uses-Source $item $runtime) -or (Uses-Source $item $database))) { throw 'HANDOFF_EXTERNAL_SOURCE_CONSUMER' }
    }
    for ($i=0;$i -lt 3;$i++) {
        $helper=@($items | Where-Object {$_.id -ceq $helperIds[$i]})[0]
        $image=$State.canonical_images[0]; if ($i -eq 2) { $image=$State.canonical_images[1] }
        if (-not (Resolve-RestoreImageMatch $helper.image $image)) { throw 'HANDOFF_HELPER_CONFIGURATION_CHANGED' }
        if ($i -eq 0) { $specs=@(@('/source/database','volume',$State.database_volume,$false),@('/source/runtime','volume',$State.runtime_volume,$false),@('/output','volume',$Backup.output_volume,$true)) }
        elseif ($i -eq 1) { $specs=@(@('/bundle','bind','',$false),@('/clone/database','volume',$Recovery.retained_clone_volumes[0],$true),@('/clone/runtime','volume',$Recovery.retained_clone_volumes[1],$true)) }
        else { $specs=,@('/var/lib/postgresql','volume',$Recovery.retained_clone_volumes[0],$true) }
        if (@($helper.mounts).Count -ne $specs.Count) { throw 'HANDOFF_HELPER_CONFIGURATION_CHANGED' }
        foreach ($spec in $specs) {
            $mount=@($helper.mounts | Where-Object {$_.Destination -ceq $spec[0]})
            if ($mount.Count -ne 1 -or $mount[0].Type -cne $spec[1] -or $mount[0].RW -ne $spec[3] -or ($spec[1] -ceq 'volume' -and $mount[0].Name -cne $spec[2])) { throw 'HANDOFF_HELPER_CONFIGURATION_CHANGED' }
        }
        $network=@(Backup-Docker @('inspect','--format','{{.HostConfig.NetworkMode}}',$helper.id) 1)
        if ($network.Count -ne 1 -or $network[0] -cne 'none') { throw 'HANDOFF_HELPER_CONFIGURATION_CHANGED' }
    }
    $exit=@(Backup-Docker @('inspect','--format','{{.State.ExitCode}} {{.State.OOMKilled}}',$Recovery.clone_container_id) 1)
    if ($exit.Count -ne 1 -or $exit[0] -cne '0 false') { throw 'HANDOFF_CLONE_EXIT_CHANGED' }
    Assert-RestoreSourceSnapshot $items @() $false
    return ,$items
}
function Assert-HandoffMediaFacts([object]$Value) {
    if ($Value.root -cne 'D:\') { throw 'HANDOFF_WINDOWS_D_DRIVE_REQUIRED' }
    foreach ($flag in @('is_boot','is_system','is_offline','is_readonly')) {
        if ($Value.$flag -isnot [bool]) { throw 'HANDOFF_MEDIA_IDENTITY_UNVERIFIED' }
        if ($Value.$flag) { throw 'HANDOFF_EXTERNAL_USB_REQUIRED' }
    }
    if ($Value.bus -cne 'USB') { throw 'HANDOFF_EXTERNAL_USB_REQUIRED' }
    if ($Value.filesystem -cne 'NTFS') { throw 'HANDOFF_NTFS_REQUIRED_NO_FORMAT_PERFORMED' }
    if ($Value.identity_sha256 -cnotmatch '^[0-9a-f]{64}$' -or ($Value.free_bytes -isnot [int] -and $Value.free_bytes -isnot [long]) -or $Value.free_bytes -lt 0) { throw 'HANDOFF_MEDIA_IDENTITY_UNVERIFIED' }
}
function Read-HandoffMedia([string]$Drive) {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $Drive -cne 'D') { throw 'HANDOFF_WINDOWS_D_DRIVE_REQUIRED' }
    $root=$Drive+':\'
    $info=New-Object IO.DriveInfo($root)
    if (-not $info.IsReady) { throw 'HANDOFF_MEDIA_NOT_READY' }
    try {
        $partition=@(Get-Partition -DriveLetter $Drive -ErrorAction Stop)
        if ($partition.Count -ne 1) { throw 'UNVERIFIED' }
        $disk=Get-Disk -Number $partition[0].DiskNumber -ErrorAction Stop
        $volume=@(Get-Volume -DriveLetter $Drive -ErrorAction Stop)
        if ($volume.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$volume[0].UniqueId) -or [string]::IsNullOrWhiteSpace([string]$disk.UniqueId)) { throw 'UNVERIFIED' }
    } catch { throw 'HANDOFF_MEDIA_IDENTITY_UNVERIFIED' }
    if (([IO.File]::GetAttributes($root) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'HANDOFF_REPARSE_POINT' }
    $facts=[pscustomobject]@{root=$root;identity_sha256=(Digest (([string]$disk.UniqueId)+'|'+([string]$volume[0].UniqueId)+'|'+([string]$disk.Size)));free_bytes=$info.AvailableFreeSpace;filesystem=$info.DriveFormat;bus=([string]$disk.BusType);is_boot=$disk.IsBoot;is_system=$disk.IsSystem;is_offline=$disk.IsOffline;is_readonly=$disk.IsReadOnly}
    Assert-HandoffMediaFacts $facts
    return $facts
}
function New-HandoffDirectory([string]$Root,[string]$Token) {
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User; $system=New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl=New-Object Security.AccessControl.DirectorySecurity
    $acl.SetOwner($sid); $acl.SetAccessRuleProtection($true,$false)
    foreach ($identity in @($sid,$system)) {
        $rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,[Security.AccessControl.FileSystemRights]::FullControl,([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit),[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $null=$acl.AddAccessRule($rule)
    }
    $path=Join-Path $Root ('WBKANG-DEMO-Offline-20261114-'+$Token)
    if ([IO.Directory]::Exists($path)) { throw 'HANDOFF_DESTINATION_EXISTS' }
    $null=[IO.Directory]::CreateDirectory($path,$acl); Assert-PrivateBackupDirectory $path
    return $path
}
function Copy-HandoffFile([string]$Source,[string]$Destination,[long]$Length,[string]$Hash) {
    $inputStream=New-Object IO.FileStream($Source,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ($inputStream.Length -ne $Length) { throw 'HANDOFF_SOURCE_CHANGED' }
        $outputStream=New-Object IO.FileStream($Destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $inputStream.CopyTo($outputStream,1048576); $outputStream.Flush($true) } finally { $outputStream.Dispose() }
    } finally { $inputStream.Dispose() }
    if ((Get-Item -LiteralPath $Destination).Length -ne $Length -or (Restore-Hash $Destination) -cne $Hash) { throw 'HANDOFF_COPY_HASH_MISMATCH' }
}
function Invoke-OfflineHandoff([string]$Root,[switch]$ExecuteHandoff,[switch]$CheckOnly) {
    $stage='OPT_IN'; $handles=@(); $destination=$null; $mutex=$null; $locked=$false
    try {
        if (($ExecuteHandoff -and $CheckOnly) -or (-not $ExecuteHandoff -and -not $CheckOnly)) { throw 'HANDOFF_EXPLICIT_MODE_REQUIRED' }
        $config=Read-RestoreJson (Join-Path $Root 'CONFIG.json')
        $stage='PINNED_BACKUP_AND_RESTORE_RECEIPTS'; Write-Output ('HANDOFF_STAGE='+$stage)
        $backup=Find-RetainedBackup $config; $restorePath=Find-RestoreEvidence $config
        $sources=@()
        foreach ($name in @('BACKUP_RECEIPT.json','BACKUP_STATE.json','manifest.json','database.tar','runtime.tar','images.tar')) { $sources+=,[pscustomobject]@{name=$name;path=(Join-Path $backup $name)} }
        $sources+=,[pscustomobject]@{name='ISOLATED_RESTORE_RECEIPT.json';path=$restorePath}
        foreach ($name in @('PRESERVATION_TOOL.zip','RESTORE_TOOL.zip','VERIFY_OFFLINE.ps1')) { $sources+=,[pscustomobject]@{name=$name;path=(Join-Path $Root $name)} }
        foreach ($source in $sources) { $handles+=,(New-Object IO.FileStream($source.path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)) }
        if ((Restore-Hash $restorePath) -cne $config.restore_receipt_sha256) { throw 'HANDOFF_RESTORE_HASH_MISMATCH' }
        $receipt=Read-RestoreJson (Join-Path $backup 'BACKUP_RECEIPT.json'); Assert-InputHashes $backup $config $receipt
        $state=Read-RestoreJson (Join-Path $backup 'BACKUP_STATE.json'); $recovery=Read-RestoreJson $restorePath
        Assert-RestoreEvidence $recovery $config
        $stage='ORIGINALS_AND_COPY_STOPPED'
        $snapshot=Assert-HandoffStoppedScope $config $receipt $state $recovery
        $records=@(); $total=0L
        foreach ($source in $sources) {
            $file=Get-Item -LiteralPath $source.path
            if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'HANDOFF_REPARSE_POINT' }
            $records+=,[pscustomobject]@{name=$source.name;bytes=$file.Length;sha256=(Restore-Hash $source.path)}; $total+=$file.Length
        }
        $stage='D_DRIVE_EXTERNAL_MEDIA_CHECK'; Write-Output ('HANDOFF_STAGE='+$stage)
        $media=Read-HandoffMedia $config.destination_drive
        if ($media.free_bytes -lt ($total+128MB)) { throw 'HANDOFF_SPACE_INSUFFICIENT' }
        if ($CheckOnly) {
            [pscustomobject]@{offline_handoff='SOURCE_HASHES_AND_EXTERNAL_MEDIA_CHECKED';destination_drive='D:';filesystem=$media.filesystem;bus=$media.bus;file_count=$records.Count;bytes_to_copy=$total;copy_performed=$false;physical_offline_custody_verified=$false;originals_or_copy_started=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
            Write-Output 'OFFLINE_HANDOFF_CHECK_PASSED_NO_COPY_NO_ORDERS'
            return 0
        }
        $mutex=New-Object Threading.Mutex($false,'Global\WBKANG_DEMO_COLD_BACKUP'); $locked=$mutex.WaitOne(0)
        if (-not $locked) { throw 'COLD_ANOTHER_BACKUP_ACTIVE' }
        $destination=New-HandoffDirectory $media.root ([Guid]::NewGuid().ToString('N'))
        $stage='COPY_TO_NEW_EXTERNAL_DIRECTORY'; Write-Output ('HANDOFF_STAGE='+$stage)
        for ($index=0;$index -lt $sources.Count;$index++) {
            $fresh=Read-HandoffMedia $config.destination_drive
            if ($fresh.identity_sha256 -cne $media.identity_sha256) { throw 'HANDOFF_MEDIA_CHANGED' }
            Copy-HandoffFile $sources[$index].path (Join-Path $destination $records[$index].name) $records[$index].bytes $records[$index].sha256
            Assert-PrivateBackupDirectory $destination; Assert-PrivateBackupFiles $destination
        }
        $stage='REVERIFY_BOTH_COPIES_AND_STOPPED_SCOPE'; Write-Output ('HANDOFF_STAGE='+$stage)
        for ($index=0;$index -lt $sources.Count;$index++) {
            if ((Restore-Hash $sources[$index].path) -cne $records[$index].sha256 -or (Restore-Hash (Join-Path $destination $records[$index].name)) -cne $records[$index].sha256) { throw 'HANDOFF_FINAL_HASH_MISMATCH' }
        }
        $fresh=Read-HandoffMedia $config.destination_drive
        if ($fresh.identity_sha256 -cne $media.identity_sha256) { throw 'HANDOFF_MEDIA_CHANGED' }
        Assert-RestoreSourceSnapshot $snapshot @() $false
        $reference=[ordered]@{protocol='DEMO_EXTERNAL_COPY_REFERENCE_V1';source_reference=$config.source_reference;backup_manifest_sha256=$config.manifest_sha256;backup_receipt_sha256=$config.receipt_sha256;restore_receipt_sha256=$config.restore_receipt_sha256;files=$records;physical_offline_custody_verified=$false;actual_http_attempts=0;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}
        Write-BackupJson (Join-Path $destination 'OFFLINE_REFERENCE.json') $reference
        $referenceHash=Restore-Hash (Join-Path $destination 'OFFLINE_REFERENCE.json')
        $verifyCommand='@echo off'+"`r`n"+'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0VERIFY_OFFLINE.ps1" -ReferenceSHA256 '+$referenceHash+"`r`n"+'exit /b %errorlevel%'+"`r`n"
        $bytes=[Text.Encoding]::ASCII.GetBytes($verifyCommand)
        $stream=New-Object IO.FileStream((Join-Path $destination 'VERIFY_OFFLINE.cmd'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }

        Write-BackupJson (Join-Path $destination 'HANDOFF_RECEIPT.json') @{status='EXTERNAL_COPY_HASHES_VERIFIED_PENDING_SAFE_EJECT';reference_sha256=$referenceHash;media_identity_sha256=$media.identity_sha256;file_count=$records.Count;bytes_copied=$total;originals_and_copy_observed_stopped=$true;physical_disconnect_verified=$false;separate_device_verification_performed=$false;automatic_retry=$false;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}
        Assert-PrivateBackupFiles $destination
        [pscustomobject]@{offline_handoff='EXTERNAL_COPY_HASHES_VERIFIED_PENDING_SAFE_EJECT';destination_directory=$destination;reference_sha256=$referenceHash;handoff_receipt_sha256=(Restore-Hash (Join-Path $destination 'HANDOFF_RECEIPT.json'));file_count=$records.Count;bytes_copied=$total;source_files_changed=$false;physical_disconnect_verified=$false;separate_device_verification_performed=$false;installation_ready=$false;originals_or_copy_started=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;orders_enabled=$false;live_trading_enabled=$false;next_action='SAFELY_EJECT_DISCONNECT_AND_STORE_SEPARATELY_RETAIN_REFERENCE_HASH'} | ConvertTo-Json -Compress | Write-Output
        Write-Output 'EXTERNAL_COPY_VERIFIED_SAFE_EJECT_AND_SEPARATE_CUSTODY_PENDING_NO_ORDERS'
        return 0
    } catch {
        $reason='HANDOFF_UNAVAILABLE'; $message=$_.Exception.Message
        $allowed=@('HANDOFF_SEARCH_LIMIT','HANDOFF_REPARSE_POINT','HANDOFF_RESTORE_RECEIPT_NOT_UNIQUE','HANDOFF_RESTORE_RECEIPT_INVALID','HANDOFF_SCOPE_INVALID','HANDOFF_UNREVIEWED_PROJECT_CONTAINER','HANDOFF_CONTAINER_NOT_STOPPED','HANDOFF_ORIGINAL_CONFIGURATION_CHANGED','HANDOFF_EXTERNAL_SOURCE_CONSUMER','HANDOFF_HELPER_CONFIGURATION_CHANGED','HANDOFF_CLONE_EXIT_CHANGED','HANDOFF_WINDOWS_D_DRIVE_REQUIRED','HANDOFF_MEDIA_NOT_READY','HANDOFF_MEDIA_IDENTITY_UNVERIFIED','HANDOFF_EXTERNAL_USB_REQUIRED','HANDOFF_NTFS_REQUIRED_NO_FORMAT_PERFORMED','HANDOFF_DESTINATION_EXISTS','HANDOFF_SOURCE_CHANGED','HANDOFF_COPY_HASH_MISMATCH','HANDOFF_EXPLICIT_MODE_REQUIRED','HANDOFF_RESTORE_HASH_MISMATCH','HANDOFF_SPACE_INSUFFICIENT','HANDOFF_MEDIA_CHANGED','HANDOFF_FINAL_HASH_MISMATCH','COLD_ANOTHER_BACKUP_ACTIVE','COLD_BACKUP_ACL_INVALID','COLD_CONTAINER_SET_CHANGED','COLD_SOURCE_RUNNING_STATE_CHANGED','COLD_SOURCE_CONFIGURATION_CHANGED','RESTORE_INPUT_HASH_MISMATCH','RESTORE_RETAINED_HASH_MISMATCH','BACKUP_PLAN_DOCKER_READ_FAILED','COLD_DOCKER_COMMAND_FAILED')
        if ($allowed -ccontains $message) { $reason=$message }
        [pscustomobject]@{offline_handoff='BLOCKED_NO_RETRY';stage=$stage;failure_category=$reason;retained_partial_destination=$destination;copy_completed=$false;physical_offline_custody_verified=$false;originals_or_copy_started=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
        return 2
    } finally {
        foreach ($handle in $handles) { $handle.Dispose() }
        if ($locked) { $mutex.ReleaseMutex() }; if ($null -ne $mutex) { $mutex.Dispose() }
    }
}
