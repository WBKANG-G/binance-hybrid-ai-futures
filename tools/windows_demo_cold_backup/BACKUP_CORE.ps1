# Functions only. The signed-by-transfer-hash launcher performs the opt-in call.
function Backup-Docker([string[]]$Arguments,[int]$Maximum=256) {
    $lines=@(& docker @Arguments 2>$null)
    if ($LASTEXITCODE -ne 0 -or $lines.Count -gt $Maximum) { throw 'COLD_DOCKER_COMMAND_FAILED' }
    foreach ($line in $lines) { if (([string]$line).Length -gt 65536) { throw 'COLD_DOCKER_OUTPUT_INVALID' } }
    return $lines
}
function Write-BackupJson([string]$Path,[object]$Value) {
    $bytes=[Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Depth 16 -Compress)+"`n")
    $stream=New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}
function New-PrivateBackupDirectory([string]$Token) {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'COLD_WINDOWS_ACL_REQUIRED' }
    $base=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if (-not [IO.Path]::IsPathRooted($base) -or $base -cne $env:LOCALAPPDATA) { throw 'COLD_LOCALAPPDATA_INVALID' }
    $drive=New-Object IO.DriveInfo([IO.Path]::GetPathRoot($base))
    if ($drive.DriveFormat -cne 'NTFS' -or $drive.AvailableFreeSpace -lt 2GB) { throw 'COLD_PRIVATE_STORAGE_UNAVAILABLE' }
    $current=New-Object IO.DirectoryInfo($base)
    while ($null -ne $current) {
        if (($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'COLD_BACKUP_PATH_REPARSE_POINT' }
        $current=$current.Parent
    }
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
    $system=New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl=New-Object Security.AccessControl.DirectorySecurity
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true,$false)
    foreach ($identity in @($sid,$system)) {
        $rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,[Security.AccessControl.FileSystemRights]::FullControl,([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit),[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $null=$acl.AddAccessRule($rule)
    }
    $parent=Join-Path $base 'WBKANG-DEMO-Backups'
    if (-not [IO.Directory]::Exists($parent)) { $null=[IO.Directory]::CreateDirectory($parent,$acl) }
    if (([IO.File]::GetAttributes($parent) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'COLD_BACKUP_PATH_REPARSE_POINT' }
    # Apply and verify DACL before creating the run directory or saving images.
    [IO.Directory]::SetAccessControl($parent,$acl)
    $destination=Join-Path $parent ('cold-20261114-'+$Token)
    if ([IO.Directory]::Exists($destination)) { throw 'COLD_BACKUP_DIRECTORY_EXISTS' }
    $null=[IO.Directory]::CreateDirectory($destination,$acl)
    Assert-PrivateBackupDirectory $destination
    return $destination
}
function Assert-PrivateBackupDirectory([string]$Path) {
    if (([IO.File]::GetAttributes($Path) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'COLD_BACKUP_PATH_REPARSE_POINT' }
    $acl=[IO.Directory]::GetAccessControl($Path)
    $user=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if (-not $acl.AreAccessRulesProtected -or $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cne $user) { throw 'COLD_BACKUP_ACL_INVALID' }
    $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 2) { throw 'COLD_BACKUP_ACL_INVALID' }
    foreach ($rule in $rules) {
        if (@($user,'S-1-5-18') -cnotcontains $rule.IdentityReference.Value -or $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or $rule.IsInherited) { throw 'COLD_BACKUP_ACL_INVALID' }
    }
}
function Assert-PrivateBackupFiles([string]$Directory) {
    $user=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    foreach ($file in @(Get-ChildItem -LiteralPath $Directory -Force)) {
        if ($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'COLD_BACKUP_ACL_INVALID' }
        $acl=[IO.File]::GetAccessControl($file.FullName)
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cne $user) { throw 'COLD_BACKUP_ACL_INVALID' }
        $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
        if ($rules.Count -ne 2) { throw 'COLD_BACKUP_ACL_INVALID' }
        foreach ($rule in $rules) { if (@($user,'S-1-5-18') -cnotcontains $rule.IdentityReference.Value -or $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl) { throw 'COLD_BACKUP_ACL_INVALID' } }
    }
}
function Sync-BackupFile([string]$Path) {
    $stream=New-Object IO.FileStream($Path,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    try { $stream.Flush($true) } finally { $stream.Dispose() }
}
function Backup-FreeBytes([string]$Directory) {
    $drive=New-Object IO.DriveInfo([IO.Path]::GetPathRoot($Directory))
    return $drive.AvailableFreeSpace
}
function Read-BackupPlan([string]$Root) {
    # Data-only mode in the current PowerShell process: no child console or line protocol.
    $script:ColdPlanDiagnostic=[pscustomobject]@{status='NO_VALID_PLAN_RESPONSE';stage='UNKNOWN';failure_category='BACKUP_PLAN_RESPONSE_INVALID';topology_probe=$null;postgres_probe=$null}
    $manifest=Join-Path $Root 'plan/TRANSFER_MANIFEST.json'
    $digest=(Get-FileHash -Algorithm SHA256 -LiteralPath $manifest).Hash.ToLowerInvariant()
    try { $values=@(& (Join-Path $Root 'plan/PLAN.ps1') -ManifestSHA256 $digest -ReturnPlanObject) }
    catch { throw 'COLD_FRESH_PLAN_BLOCKED' }
    if ($values.Count -ne 1 -or $values[0] -isnot [pscustomobject]) { throw 'COLD_FRESH_PLAN_BLOCKED' }
    $plan=$values[0]
    foreach ($flag in @('source_services_stopped','source_restart_policies_changed','database_changed','files_changed','backup_created','execution_approved','orders_enabled','live_trading_enabled')) {
        if ($plan.$flag -isnot [bool] -or $plan.$flag -ne $false) { throw 'COLD_FRESH_PLAN_BLOCKED' }
    }
    $probe=$plan.postgres_probe
    $probeFields=@('pgdata','database_user','database_name','server_version','control_version')
    $probeValues=@('NOT_CHECKED','READ_FAILED','OBSERVED','UNSUPPORTED_LAYOUT','SUPPORTED_LAYOUT','INVALID_FORMAT','VALID_IDENTIFIER','UNSUPPORTED_VERSION','SUPPORTED_VERSION','VERSION_MISMATCH_OR_INVALID','MATCHING_VERSION')
    if ($probe -isnot [pscustomobject] -or (@($probe.PSObject.Properties.Name | Sort-Object) -join '|') -cne (@($probeFields | Sort-Object) -join '|')) { throw 'COLD_FRESH_PLAN_BLOCKED' }
    foreach ($field in $probeFields) { if ($probe.$field -isnot [string] -or $probeValues -cnotcontains $probe.$field) { throw 'COLD_FRESH_PLAN_BLOCKED' } }
    $topology=$null
    if ($null -ne $plan.PSObject.Properties['topology_probe']) {
        $topology=$plan.topology_probe
        $operations=@('NOT_STARTED','LIST_CONTAINERS','INSPECT_CONTAINER','PARSE_JSON_FIELD','VALIDATE_FIELD','CONTAINER_VALIDATED','INSPECT_VOLUME')
        $fields=@('NONE','IDS','RECORD','VOLUME_RECORD','id','name','image','labels','mounts','running','restarting','paused','dead','restart','networks','driver','scope','options')
        if ($topology -isnot [pscustomobject] -or (@($topology.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'container_ref|field|operation' -or $operations -cnotcontains $topology.operation -or $fields -cnotcontains $topology.field -or ($null -ne $topology.container_ref -and ($topology.container_ref -isnot [string] -or $topology.container_ref -cnotmatch '^[0-9a-f]{64}$'))) { throw 'COLD_FRESH_PLAN_BLOCKED' }
    }
    if ($plan.backup_plan -ceq 'READ_ONLY_COLD_BACKUP_PLAN_OBSERVED_KEEP_PAUSED') {
        $script:ColdPlanDiagnostic=[pscustomobject]@{status='PLAN_OBSERVED';stage='COMPLETE';failure_category='NONE';topology_probe=$topology;postgres_probe=$probe}
        return $plan
    }
    $stages=@('TRANSFER_VERIFICATION','CONTAINER_TOPOLOGY','POSTGRES_CAPABILITY','PERSISTENT_VOLUME_SCOPE','CONTEXT_RECHECK')
    $codes=@('BACKUP_PLAN_TOPOLOGY_JSON_INVALID','BACKUP_PLAN_TOPOLOGY_FIELD_INVALID','BACKUP_PLAN_TOPOLOGY_FIELD_COUNT','BACKUP_PLAN_TRANSFER_INVALID','BACKUP_PLAN_DOCKER_READ_FAILED','BACKUP_PLAN_CONTEXT_INVALID','BACKUP_PLAN_UNSTABLE_CONTAINER','BACKUP_PLAN_VOLUME_LAYOUT_UNSUPPORTED','BACKUP_PLAN_VOLUME_OPTIONS_REQUIRE_REVIEW','BACKUP_PLAN_SOURCE_CONTAINER_REQUIRED','BACKUP_PLAN_POSTGRES_LAYOUT_UNSUPPORTED','BACKUP_PLAN_POSTGRES_FIELD_UNAVAILABLE','BACKUP_PLAN_KEY_SCOPE_INVALID','BACKUP_PLAN_EXTERNAL_VOLUME_CONSUMER','BACKUP_PLAN_EXTERNAL_DB_NETWORK_CONSUMER','BACKUP_PLAN_UNKNOWN_PROJECT_SERVICE','BACKUP_PLAN_CONTEXT_CHANGED','BACKUP_PLAN_RUNNER_FAILED')
    if ($plan.backup_plan -ceq 'BLOCKED_KEEP_PAUSED' -and $stages -ccontains $plan.stage -and $codes -ccontains $plan.failure_category) {
        $script:ColdPlanDiagnostic=[pscustomobject]@{status='PLAN_BLOCKED';stage=$plan.stage;failure_category=$plan.failure_category;topology_probe=$topology;postgres_probe=$probe}
    }
    throw 'COLD_FRESH_PLAN_BLOCKED'
}
function Assert-ReviewedBackupScope([object]$Plan,[object]$Config) {
    if ($Plan.api_container_id -cne $Config.api_container_id -or $Plan.database_container_id -cne $Config.database_container_id -or $Plan.postgresql_major -ne 18 -or $Plan.excluded_key_session_containers -ne 1 -or $Plan.participants.Count -ne 6) { throw 'COLD_REVIEWED_SCOPE_CHANGED' }
    $expected=@{api=3;db=1;alerts=1;web=1}
    foreach ($role in $expected.Keys) { if (@($Plan.participants | Where-Object { $_.service -ceq $role }).Count -ne $expected[$role]) { throw 'COLD_REVIEWED_SCOPE_CHANGED' } }
    foreach ($item in $Plan.participants) {
        if ($item.container_id -cnotmatch '^[0-9a-f]{64}$' -or $item.restart_policy -cne 'no' -or $item.restart_maximum_retry_count -ne 0) { throw 'COLD_RESTART_POLICY_OR_SCOPE_CHANGED' }
        $shouldRun=@($Config.api_container_id,$Config.database_container_id) -ccontains $item.container_id
        if ($item.running -isnot [bool] -or $item.running -ne $shouldRun) { throw 'COLD_REVIEWED_RUNNING_SET_CHANGED' }
    }
    if (@($Plan.participants | Where-Object {$_.uses_database_volume}).Count -ne 1 -or @($Plan.participants | Where-Object {$_.uses_runtime_volume}).Count -ne 3) { throw 'COLD_REVIEWED_SCOPE_CHANGED' }
}
function Invoke-PauseHeader([string]$Root,[string]$Id) {
    $source=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'account_header.py')))
    $payload="import base64`nexec(compile(base64.b64decode('$source'),'<backup-pause-header>','exec'))"
    $lines=@($payload | & docker exec -i $Id python -I -B - 2>$null)
    if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 1024) { throw 'COLD_PAUSE_OR_LEASE_CHECK_BLOCKED' }
    $value=$lines[0] | ConvertFrom-Json
    if ((@($value.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'active_lease|database_changed|orders|paused|status|unresolved_requests' -or $value.status -cne 'PAUSED_NO_ACTIVE_LEASE' -or $value.paused -isnot [bool] -or -not $value.paused -or $value.active_lease -isnot [bool] -or $value.active_lease -or $value.database_changed -isnot [bool] -or $value.database_changed) { throw 'COLD_PAUSE_OR_LEASE_CHECK_BLOCKED' }
    foreach ($name in @('orders','unresolved_requests')) { if (($value.$name -isnot [int] -and $value.$name -isnot [long]) -or $value.$name -lt 0 -or $value.$name -gt 768) { throw 'COLD_PAUSE_OR_LEASE_CHECK_BLOCKED' } }
    if ($value.orders -ne 34 -or $value.unresolved_requests -ne 4) { throw 'COLD_REVIEWED_ACCOUNT_COUNTS_CHANGED' }
    return $value
}
function Assert-SourceSnapshot([object[]]$Original,[string[]]$Helpers,[bool]$Frozen) {
    $current=@(Container-Ids)
    $expected=@(@($Original | ForEach-Object {$_.id})+$Helpers | Sort-Object)
    if (($current -join '|') -cne ($expected -join '|')) { throw 'COLD_CONTAINER_SET_CHANGED' }
    foreach ($old in $Original) {
        $fresh=Read-Container $old.id
        $expectedRunning=$old.running
        if ($Frozen -and (Project $old) -ceq 'hybrid-windows' -and @('api','db','web','alerts') -ccontains (Service $old)) { $expectedRunning=$false }
        if ($fresh.running -ne $expectedRunning -or $fresh.restarting -or $fresh.paused -or $fresh.dead) { throw 'COLD_SOURCE_RUNNING_STATE_CHANGED' }
        if ($Frozen -and $old.running -and -not $expectedRunning) {
            $oldKeys=@($old.networks.PSObject.Properties | ForEach-Object {$_.Name} | Sort-Object)
            $newKeys=@($fresh.networks.PSObject.Properties | ForEach-Object {$_.Name} | Sort-Object)
            if (($oldKeys -join '|') -cne ($newKeys -join '|')) { throw 'COLD_SOURCE_CONFIGURATION_CHANGED' }
            foreach ($key in $oldKeys) { if ($fresh.networks.$key.NetworkID -cne $old.networks.$key.NetworkID) { throw 'COLD_SOURCE_CONFIGURATION_CHANGED' } }
            # Docker clears endpoint IP/MAC/ID on stop; retain membership checks above.
            $fresh.networks=$old.networks
        }
        $fresh.running=$old.running
        if (($fresh | ConvertTo-Json -Depth 12 -Compress) -cne ($old | ConvertTo-Json -Depth 12 -Compress)) { throw 'COLD_SOURCE_CONFIGURATION_CHANGED' }
    }
}
function Request-GracefulSourceStop([string]$Id,[string]$Signal,[int]$Seconds) {
    if (@('SIGTERM','SIGINT') -cnotcontains $Signal -or $Seconds -lt 1 -or $Seconds -gt 60) { throw 'COLD_STOP_POLICY_INVALID' }
    $null=Backup-Docker @('kill',('--signal='+$Signal),$Id) 1
    $watch=[Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -le $Seconds) {
        $state=@(Backup-Docker @('inspect','--format','{{.State.Running}}',$Id) 1)
        if ($state.Count -ne 1 -or @('true','false') -cnotcontains $state[0]) { throw 'COLD_DOCKER_OUTPUT_INVALID' }
        if ($state[0] -ceq 'false') { return }
        Start-Sleep -Milliseconds 500
    }
    throw 'COLD_GRACEFUL_STOP_TIMEOUT'
}
function Assert-WorkerReport([object]$Value) {
    if ((@($Value.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'archives|failure_category|file_counts|manifest_sha256|source_changed|status' -or $Value.status -cne 'COLD_ARCHIVES_CREATED' -or $Value.failure_category -cne 'NONE' -or $Value.source_changed -isnot [bool] -or $Value.source_changed -or $Value.manifest_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'COLD_WORKER_OUTPUT_INVALID' }
    foreach ($name in @('archives','file_counts')) { if ((@($Value.$name.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'database|runtime') { throw 'COLD_WORKER_OUTPUT_INVALID' } }
    foreach ($group in @('database','runtime')) {
        $entry=$Value.archives.$group
        if ((@($entry.PSObject.Properties.Name | Sort-Object) -join '|') -cne 'bytes|sha256' -or $entry.sha256 -cnotmatch '^[0-9a-f]{64}$' -or ($entry.bytes -isnot [int] -and $entry.bytes -isnot [long]) -or $entry.bytes -lt 10240 -or $entry.bytes -gt 9GB -or ($Value.file_counts.$group -isnot [int] -and $Value.file_counts.$group -isnot [long]) -or $Value.file_counts.$group -lt 1 -or $Value.file_counts.$group -gt 50000) { throw 'COLD_WORKER_OUTPUT_INVALID' }
    }
}
function Invoke-ColdBackup([string]$Root,[switch]$ExecuteColdBackup,[switch]$CheckOnly) {
    $stage='OPT_IN'; $directory=$null; $stopAttempted=$false; $stopped=$false; $imagesSaved=$false; $helpers=@(); $outputVolume=$null; $mutex=$null; $locked=$false
    $script:ColdPlanDiagnostic=$null
    try {
        if ($ExecuteColdBackup -and $CheckOnly) { throw 'COLD_EXECUTION_MODE_INVALID' }
        if (-not $ExecuteColdBackup -and -not $CheckOnly) { throw 'COLD_EXPLICIT_EXECUTION_REQUIRED' }
        $config=Get-Content -Raw -LiteralPath (Join-Path $Root 'BACKUP_CONFIG.json') | ConvertFrom-Json
        $stage='FRESH_READ_ONLY_PLAN'; Write-Output ('BACKUP_STAGE='+$stage)
        $plan=Read-BackupPlan $Root
        Assert-ReviewedBackupScope $plan $config
        $api=Read-Container $plan.api_container_id; $db=Read-Container $plan.database_container_id
        $runtime=Volume-Mount $api '/app/demo-export-state'; $database=Volume-Mount $db $plan.database_mount_destination
        $pgdata=@(Docker-Lines @('exec',$db.id,'printenv','PGDATA') 1)
        if ($pgdata.Count -ne 1 -or $pgdata[0] -cnotmatch '^/var/lib/postgresql/(data|[0-9]{1,2}/docker)$' -or ($pgdata[0] -cne $database.Destination -and -not $pgdata[0].StartsWith(($database.Destination+'/'),[StringComparison]::Ordinal))) { throw 'COLD_PGDATA_CHANGED' }
        $relative=$pgdata[0].Substring($database.Destination.Length).TrimStart('/')
        $ids=@(Container-Ids); $original=@($ids | ForEach-Object {Read-Container $_})
        if ((Digest ($original | ConvertTo-Json -Depth 12 -Compress)) -cne $plan.source_context_sha256) { throw 'COLD_SOURCE_CONFIGURATION_CHANGED' }
        $keyOwners=@($original | Where-Object { (Project $_) -ceq 'hybrid-windows' -and (Service $_) -ceq 'key-session' })
        if ($keyOwners.Count -ne 1 -or -not $keyOwners[0].running) { throw 'COLD_KEY_SESSION_OWNER_NOT_RUNNING' }
        if ($CheckOnly) {
            $header=Invoke-PauseHeader $Root $api.id
            Assert-SourceSnapshot $original @() $false
            [pscustomobject]@{cold_backup='READ_ONLY_PRECHECK_PASSED_KEEP_PAUSED';fresh_plan=$script:ColdPlanDiagnostic;source_stop_attempted=$false;source_services_stopped=$false;images_saved=$false;backup_created=$false;account_pause_rechecked=$true;windows_acl_checked=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Depth 8 -Compress | Write-Output
            Write-Output 'COLD_BACKUP_CHECK_PASSED_NO_STOP_NO_BACKUP_NO_ORDERS'
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
        $stage='FINAL_PAUSE_AND_SOURCE_CHECK'; Write-Output ('BACKUP_STAGE='+$stage)
        $header=Invoke-PauseHeader $Root $api.id
        Assert-SourceSnapshot $original @() $false
        $participantIds=@($plan.participants | ForEach-Object {$_.container_id})
        $retainedSnapshots=@($original | Where-Object { $participantIds -ccontains $_.id } | ForEach-Object {
            [pscustomobject]@{id=$_.id;name=$_.name;image=$_.image;service=(Service $_);running=$_.running;restart=$_.restart;mounts=@($_.mounts | Where-Object { $_.Destination -cne '/run/demo-keys' });network_names=@($_.networks.PSObject.Properties | ForEach-Object {$_.Name})}
        })
        $state=[ordered]@{protocol='WINDOWS_COLD_BACKUP_STATE_V1';source_reference=$config.source_reference;plan=$plan;source_snapshots=$retainedSnapshots;all_container_ids_sha256=(Digest ($ids -join "`n"));key_session_excluded=$true;pgdata=$pgdata[0];database_volume=$database.Name;runtime_volume=$runtime.Name;canonical_images=$imageIds;image_archive_sha256=$imageHash;pause_header=$header;automatic_resume_authorized=$false}
        Write-BackupJson (Join-Path $directory 'BACKUP_STATE.json') $state
        Assert-PrivateBackupFiles $directory
        $stage='STOP_WRITERS_THEN_DATABASE'; Write-Output ('BACKUP_STAGE='+$stage)
        foreach ($participant in @($plan.participants | Where-Object {$_.service -cne 'db'})) {
            if ($participant.running) {
                $stopAttempted=$true
                Write-BackupJson (Join-Path $directory ('STOP_ATTEMPT_'+$participant.container_id+'.json')) @{container_id=$participant.container_id;automatic_resume=$false}
                Request-GracefulSourceStop $participant.container_id 'SIGTERM' 30
            }
        }
        $stopAttempted=$true
        Write-BackupJson (Join-Path $directory ('STOP_ATTEMPT_'+$db.id+'.json')) @{container_id=$db.id;automatic_resume=$false}
        Request-GracefulSourceStop $db.id 'SIGINT' 60
        Assert-SourceSnapshot $original @() $true; $stopped=$true
        $exit=@(Backup-Docker @('inspect','--format','{{.State.ExitCode}} {{.State.OOMKilled}}',$db.id) 1)
        if ($exit.Count -ne 1 -or $exit[0] -cne '0 false') { throw 'COLD_DATABASE_NOT_CLEANLY_STOPPED' }
        $stage='VERIFY_PG_CONTROL'; Write-Output ('BACKUP_STAGE='+$stage)
        $control=@(Backup-Docker @('create','--pull=never','--network','none','--restart','no','--read-only','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--security-opt','no-new-privileges','--user','0:0','--env','LC_ALL=C','--mount',('type=volume,source='+$database.Name+',target='+$database.Destination+',readonly,volume-nocopy'),'--entrypoint','pg_controldata',$imageIds[1],$pgdata[0]) 1)
        if ($control.Count -ne 1 -or $control[0] -cnotmatch '^[0-9a-f]{64}$') { throw 'COLD_HELPER_ID_INVALID' }; $helpers+=,$control[0]
        $controlOutput=@(Backup-Docker @('start','--attach',$control[0]) 64)
        if (@($controlOutput | Where-Object {$_ -cmatch '^Database cluster state:\s+shut down$'}).Count -ne 1) { throw 'COLD_PG_CONTROL_NOT_SHUT_DOWN' }
        Assert-SourceSnapshot $original $helpers $true
        $stage='CREATE_COLD_ARCHIVES'; Write-Output ('BACKUP_STAGE='+$stage)
        $outputVolume='demo-cold-backup-'+$token
        $volume=@(Backup-Docker @('volume','create',$outputVolume) 1)
        if ($volume.Count -ne 1 -or $volume[0] -cne $outputVolume) { throw 'COLD_OUTPUT_VOLUME_INVALID' }
        $worker=@(Backup-Docker @('create','--pull=never','--interactive','--network','none','--restart','no','--read-only','--memory','512m','--pids-limit','32','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--security-opt','no-new-privileges','--user','0:0','--mount',('type=volume,source='+$database.Name+',target=/source/database,readonly,volume-nocopy'),'--mount',('type=volume,source='+$runtime.Name+',target=/source/runtime,readonly,volume-nocopy'),'--mount',('type=volume,source='+$outputVolume+',target=/output,volume-nocopy'),'--entrypoint','python',$imageIds[0],'-I','-B','-') 1)
        if ($worker.Count -ne 1 -or $worker[0] -cnotmatch '^[0-9a-f]{64}$') { throw 'COLD_HELPER_ID_INVALID' }; $helpers+=,$worker[0]
        $archive=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'cold_archive.py')))
        $driver=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'backup_driver.py')))
        $workerConfig=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((@{pgdata_relative=$relative;postgresql_major=18;source_control_clean=$true} | ConvertTo-Json -Compress)))
        $payload="import base64,json`nARCHIVE_SOURCE=base64.b64decode('$archive')`nCONFIG=json.loads(base64.b64decode('$workerConfig'))`nexec(compile(base64.b64decode('$driver'),'<cold-backup-driver>','exec'))"
        $lines=@($payload | & docker start --attach --interactive $worker[0] 2>$null)
        $workerExit=$LASTEXITCODE
        if ($lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 4096) { throw 'COLD_ARCHIVE_WORKER_FAILED' }
        $created=$lines[0] | ConvertFrom-Json
        $workerFailures=@('COLD_POSTMASTER_PID_PRESENT','COLD_PG_VERSION_INVALID','COLD_OUTPUT_SPACE_INSUFFICIENT','COLD_COUNT_LIMIT','COLD_LINK_OR_SPECIAL_FILE','COLD_EXTENDED_METADATA_UNSUPPORTED','COLD_SOURCE_CHANGED','COLD_BYTE_LIMIT','COLD_FILE_LIMIT','COLD_DEPTH_LIMIT','COLD_TIME_LIMIT','COLD_OUTPUT_NOT_EMPTY','COLD_MODE_INVALID','COLD_OWNER_INVALID')
        if ($workerExit -ne 0) {
            if ($created.status -ceq 'BLOCKED' -and $workerFailures -ccontains $created.failure_category) { throw $created.failure_category }
            throw 'COLD_ARCHIVE_WORKER_FAILED'
        }
        Assert-WorkerReport $created
        Assert-SourceSnapshot $original $helpers $true
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
        Assert-PrivateBackupDirectory $directory; Assert-SourceSnapshot $original $helpers $true
        $receipt=[ordered]@{status='COLD_BACKUP_LOCAL_VERIFIED_KEEP_STOPPED';source_reference=$config.source_reference;manifest_sha256=$created.manifest_sha256;image_archive_sha256=$imageHash;state_sha256=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $directory 'BACKUP_STATE.json')).Hash.ToLowerInvariant();archives=$created.archives;file_counts=$created.file_counts;output_volume=$outputVolume;helper_container_ids=$helpers;source_services_stopped=$true;postgres_clean_shutdown_verified=$true;docker_source_quiescence_observed=$true;source_data_archive_restore_tested=$false;offline_handoff_performed=$false;periods_or_baselines_changed=$false;source_restart_policies_changed=$false;automatic_resume=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
        Write-BackupJson (Join-Path $directory 'BACKUP_RECEIPT.json') $receipt
        Assert-PrivateBackupFiles $directory
        $receiptHash=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $directory 'BACKUP_RECEIPT.json')).Hash.ToLowerInvariant()
        [pscustomobject]@{cold_backup='LOCAL_ARCHIVES_AND_IMAGES_VERIFIED_KEEP_STOPPED';private_backup_directory=$directory;manifest_sha256=$created.manifest_sha256;receipt_sha256=$receiptHash;source_services_stopped=$true;original_services_restarted=$false;backup_created=$true;local_copy_hashes_verified=$true;restore_tested=$false;offline_handoff_performed=$false;installation_ready=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;next_action='KEEP_STOPPED_REVIEW_RECEIPT_THEN_ISOLATED_RESTORE_NO_RETRY'} | ConvertTo-Json -Depth 6 -Compress | Write-Output
        Write-Output 'COLD_BACKUP_SAVED_LOCAL_VERIFIED_KEEP_STOPPED_NO_ORDERS'
        return 0
    } catch {
        $reason='COLD_BACKUP_UNAVAILABLE'
        $allowed=@('COLD_EXECUTION_MODE_INVALID','COLD_KEY_SESSION_OWNER_NOT_RUNNING','COLD_POSTMASTER_PID_PRESENT','COLD_PG_VERSION_INVALID','COLD_OUTPUT_SPACE_INSUFFICIENT','COLD_COUNT_LIMIT','COLD_LINK_OR_SPECIAL_FILE','COLD_EXTENDED_METADATA_UNSUPPORTED','COLD_SOURCE_CHANGED','COLD_BYTE_LIMIT','COLD_FILE_LIMIT','COLD_DEPTH_LIMIT','COLD_TIME_LIMIT','COLD_OUTPUT_NOT_EMPTY','COLD_MODE_INVALID','COLD_OWNER_INVALID','COLD_STOP_POLICY_INVALID','COLD_GRACEFUL_STOP_TIMEOUT','COLD_EXPLICIT_EXECUTION_REQUIRED','COLD_WINDOWS_ACL_REQUIRED','COLD_LOCALAPPDATA_INVALID','COLD_PRIVATE_STORAGE_UNAVAILABLE','COLD_BACKUP_PATH_REPARSE_POINT','COLD_BACKUP_DIRECTORY_EXISTS','COLD_BACKUP_ACL_INVALID','COLD_FRESH_PLAN_BLOCKED','COLD_REVIEWED_SCOPE_CHANGED','COLD_RESTART_POLICY_OR_SCOPE_CHANGED','COLD_REVIEWED_RUNNING_SET_CHANGED','COLD_PAUSE_OR_LEASE_CHECK_BLOCKED','COLD_REVIEWED_ACCOUNT_COUNTS_CHANGED','COLD_CONTAINER_SET_CHANGED','COLD_SOURCE_RUNNING_STATE_CHANGED','COLD_SOURCE_CONFIGURATION_CHANGED','COLD_WORKER_OUTPUT_INVALID','COLD_PGDATA_CHANGED','COLD_ANOTHER_BACKUP_ACTIVE','COLD_HOST_SPACE_INSUFFICIENT','COLD_IMAGE_ID_INVALID','COLD_DOCKER_COMMAND_FAILED','COLD_DOCKER_OUTPUT_INVALID','COLD_DATABASE_NOT_CLEANLY_STOPPED','COLD_HELPER_ID_INVALID','COLD_PG_CONTROL_NOT_SHUT_DOWN','COLD_OUTPUT_VOLUME_INVALID','COLD_ARCHIVE_WORKER_FAILED','COLD_LOCAL_ARCHIVE_HASH_MISMATCH','COLD_LOCAL_MANIFEST_HASH_MISMATCH','COLD_LOCAL_IMAGE_HASH_MISMATCH')
        if ($allowed -ccontains $_.Exception.Message) { $reason=$_.Exception.Message }
        if ($null -ne $directory) { try { Write-BackupJson (Join-Path $directory 'FAILURE.json') @{stage=$stage;failure_category=$reason;source_stop_attempted=$stopAttempted;retained_helper_ids=$helpers;retained_output_volume=$outputVolume;automatic_resume=$false} } catch {} }
        [pscustomobject]@{cold_backup='BLOCKED_KEEP_STOPPED_NO_AUTOMATIC_RETRY';stage=$stage;failure_category=$reason;fresh_plan=$script:ColdPlanDiagnostic;private_backup_directory=$directory;source_stop_attempted=$stopAttempted;source_state_may_have_changed=$stopAttempted;source_services_stopped=$stopped;images_saved=$imagesSaved;retained_helper_ids=$helpers;retained_output_volume=$outputVolume;backup_completed=$false;automatic_resume=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Depth 8 -Compress | Write-Output
        return 2
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }; if ($null -ne $mutex) { $mutex.Dispose() }
    }
}
