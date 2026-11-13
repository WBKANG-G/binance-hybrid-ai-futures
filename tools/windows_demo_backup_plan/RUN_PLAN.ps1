param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=$PSScriptRoot
$stage='TRANSFER_VERIFICATION'
$oldOutput=$OutputEncoding
$oldConsole=[Console]::OutputEncoding
$OutputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
function Digest([string]$Text) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Docker-Lines([string[]]$Arguments,[int]$Maximum=128) {
    $lines=@(& docker @Arguments 2>$null)
    if ($LASTEXITCODE -ne 0 -or $lines.Count -gt $Maximum) { throw 'BACKUP_PLAN_DOCKER_READ_FAILED' }
    foreach ($line in $lines) { if (([string]$line).Length -gt 65536) { throw 'BACKUP_PLAN_DOCKER_READ_FAILED' } }
    return $lines
}
function Container-Ids {
    $values=@(Docker-Lines @('ps','--all','--quiet','--no-trunc'))
    if ($values.Count -eq 0 -or @($values | Select-Object -Unique).Count -ne $values.Count) { throw 'BACKUP_PLAN_CONTEXT_INVALID' }
    foreach ($id in $values) { if ($id -isnot [string] -or $id -cnotmatch '^[0-9a-f]{64}$') { throw 'BACKUP_PLAN_CONTEXT_INVALID' } }
    return @($values | Sort-Object)
}
function Read-Container([string]$Id) {
    $format='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"labels":{{json .Config.Labels}},"mounts":{{json .Mounts}},"running":{{json .State.Running}},"restarting":{{json .State.Restarting}},"paused":{{json .State.Paused}},"dead":{{json .State.Dead}},"restart":{{json .HostConfig.RestartPolicy}},"networks":{{json .NetworkSettings.Networks}}}'
    $lines=@(Docker-Lines @('inspect','--format',$format,$Id) 1)
    if ($lines.Count -ne 1) { throw 'BACKUP_PLAN_CONTEXT_INVALID' }
    $value=$lines[0] | ConvertFrom-Json
    if ($value -isnot [pscustomobject] -or $value.id -cne $Id -or $value.image -cnotmatch '^sha256:[0-9a-f]{64}$' -or $value.name -isnot [string]) { throw 'BACKUP_PLAN_CONTEXT_INVALID' }
    foreach ($flag in @('running','restarting','paused','dead')) { if ($value.$flag -isnot [bool]) { throw 'BACKUP_PLAN_CONTEXT_INVALID' } }
    if (@('no','always','unless-stopped','on-failure') -cnotcontains $value.restart.Name -or ($value.restart.MaximumRetryCount -isnot [int] -and $value.restart.MaximumRetryCount -isnot [long]) -or $value.restart.MaximumRetryCount -lt 0 -or $value.restart.MaximumRetryCount -gt 100000) { throw 'BACKUP_PLAN_CONTEXT_INVALID' }
    if ($null -ne $value.mounts -and $value.mounts -isnot [System.Array]) { throw 'BACKUP_PLAN_CONTEXT_INVALID' }
    if (@($value.mounts).Count -gt 32) { throw 'BACKUP_PLAN_CONTEXT_INVALID' }
    foreach ($mount in @($value.mounts)) {
        if ($null -eq $mount) { continue }
        if ($mount.Type -isnot [string] -or $mount.Source -isnot [string] -or $mount.Destination -isnot [string] -or $mount.RW -isnot [bool] -or $mount.Source.Length -gt 4096 -or $mount.Destination.Length -gt 1024) { throw 'BACKUP_PLAN_CONTEXT_INVALID' }
    }
    return $value
}
function Project([object]$Item) {
    if ($null -eq $Item.labels -or $Item.labels -isnot [pscustomobject]) { return '' }
    $prop=$Item.labels.PSObject.Properties['com.docker.compose.project']
    if ($null -eq $prop -or $prop.Value -isnot [string]) { return '' }
    return $prop.Value
}
function Service([object]$Item) {
    if ($null -eq $Item.labels -or $Item.labels -isnot [pscustomobject]) { return '' }
    $prop=$Item.labels.PSObject.Properties['com.docker.compose.service']
    if ($null -eq $prop -or $prop.Value -isnot [string]) { return '' }
    return $prop.Value
}
function Volume-Mount([object]$Item,[string]$Destination) {
    $selected=@($Item.mounts | Where-Object { $_.Destination -ceq $Destination })
    if ($selected.Count -ne 1) { throw 'BACKUP_PLAN_VOLUME_LAYOUT_UNSUPPORTED' }
    $mount=$selected[0]
    if ($mount.Type -cne 'volume' -or $mount.RW -ne $true -or $mount.Name -isnot [string] -or $mount.Name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$' -or -not $mount.Source.StartsWith('/') -or $mount.Source -ceq '/') { throw 'BACKUP_PLAN_VOLUME_LAYOUT_UNSUPPORTED' }
    $format='{"driver":{{json .Driver}},"scope":{{json .Scope}},"options":{{json .Options}}}'
    $lines=@(Docker-Lines @('volume','inspect','--format',$format,$mount.Name) 1)
    if ($lines.Count -ne 1) { throw 'BACKUP_PLAN_VOLUME_LAYOUT_UNSUPPORTED' }
    $volume=$lines[0] | ConvertFrom-Json
    if ($volume.driver -cne 'local' -or $volume.scope -cne 'local') { throw 'BACKUP_PLAN_VOLUME_LAYOUT_UNSUPPORTED' }
    if ($null -ne $volume.options -and @($volume.options.PSObject.Properties).Count -ne 0) { throw 'BACKUP_PLAN_VOLUME_OPTIONS_REQUIRE_REVIEW' }
    return $mount
}
function Uses-Source([object]$Item,[object]$Mount) {
    foreach ($other in @($Item.mounts)) {
        if ($null -eq $other) { continue }
        $a=$other.Source.TrimEnd('/')
        $b=$Mount.Source.TrimEnd('/')
        if ($a -ceq $b -or $a.StartsWith(($b+'/'),[StringComparison]::Ordinal) -or ($a -ne '' -and $b.StartsWith(($a+'/'),[StringComparison]::Ordinal))) { return $true }
    }
    return $false
}
try {
    if ($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$') { throw 'BACKUP_PLAN_TRANSFER_INVALID' }
    $path=Join-Path $root 'TRANSFER_MANIFEST.json'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -cne $ManifestSHA256) { throw 'BACKUP_PLAN_TRANSFER_INVALID' }
    $manifest=Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
    $required=@('PLAN.ps1','PLAN_CONFIG.json','README_KO.md')
    if ((@($manifest.files.PSObject.Properties.Name | Sort-Object) -join '|') -cne (@($required | Sort-Object) -join '|')) { throw 'BACKUP_PLAN_TRANSFER_INVALID' }
    foreach ($entry in $manifest.files.PSObject.Properties) {
        if ($entry.Value -isnot [string] -or $entry.Value -cnotmatch '^[0-9a-f]{64}$') { throw 'BACKUP_PLAN_TRANSFER_INVALID' }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $root $entry.Name)).Hash.ToLowerInvariant() -cne $entry.Value) { throw 'BACKUP_PLAN_TRANSFER_INVALID' }
    }
    Write-Output 'TRANSFER_FILES_VERIFIED'
    $configuration=Get-Content -Raw -LiteralPath (Join-Path $root 'PLAN_CONFIG.json') | ConvertFrom-Json
    if ($configuration.source_reference -cnotmatch '^[0-9a-f]{40}$' -or $configuration.project -cne 'hybrid-windows') { throw 'BACKUP_PLAN_TRANSFER_INVALID' }
    $stage='CONTAINER_TOPOLOGY'
    $ids=@(Container-Ids)
    $items=@()
    foreach ($id in $ids) { $items+=,(Read-Container $id) }
    $project=@($items | Where-Object { (Project $_) -ceq 'hybrid-windows' })
    $api=@($project | Where-Object { $_.name -ceq '/hybrid-windows-api-1' -and (Service $_) -ceq 'api' })
    $db=@($project | Where-Object { (Service $_) -ceq 'db' })
    if ($api.Count -ne 1 -or $db.Count -ne 1 -or -not $api[0].running -or -not $db[0].running) { throw 'BACKUP_PLAN_SOURCE_CONTAINER_REQUIRED' }
    $api=$api[0]
    $db=$db[0]
    $stage='POSTGRES_CAPABILITY'
    # These five nonsensitive fields remain in memory; no environment dump or passwords.
    $command='printf "%s\n%s\n%s\n" "$PGDATA" "$POSTGRES_USER" "$POSTGRES_DB"; postgres --version; pg_controldata --version'
    $properties=@(Docker-Lines @('exec',$db.id,'sh','-c',$command) 5)
    if ($properties.Count -ne 5 -or $properties[0] -cnotmatch '^/var/lib/postgresql/(data|[0-9]{1,2}/docker)$' -or $properties[1] -cnotmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$' -or $properties[2] -cnotmatch '^[A-Za-z_][A-Za-z0-9_]{0,62}$') { throw 'BACKUP_PLAN_POSTGRES_LAYOUT_UNSUPPORTED' }
    if ($properties[3] -cnotmatch '^postgres \(PostgreSQL\) (1[5-8])(?:\.[0-9]+)?(?: .*)?$') { throw 'BACKUP_PLAN_POSTGRES_LAYOUT_UNSUPPORTED' }
    $major=[int]$Matches[1]
    if ($properties[4] -cnotmatch ('^pg_controldata \(PostgreSQL\) '+$major+'(?:\.[0-9]+)?(?: .*)?$')) { throw 'BACKUP_PLAN_POSTGRES_LAYOUT_UNSUPPORTED' }
    $stage='PERSISTENT_VOLUME_SCOPE'
    $runtime=Volume-Mount $api '/app/demo-export-state'
    $dbMounts=@($db.mounts)
    if ($dbMounts.Count -ne 1 -or @('/var/lib/postgresql','/var/lib/postgresql/data') -cnotcontains $dbMounts[0].Destination) { throw 'BACKUP_PLAN_VOLUME_LAYOUT_UNSUPPORTED' }
    $database=Volume-Mount $db $dbMounts[0].Destination
    $pgdata=[string]$properties[0]
    if ($pgdata -cne $database.Destination -and -not $pgdata.StartsWith(($database.Destination+'/'),[StringComparison]::Ordinal)) { throw 'BACKUP_PLAN_POSTGRES_LAYOUT_UNSUPPORTED' }
    if ($database.Name -ceq $runtime.Name -or $database.Source -ceq $runtime.Source) { throw 'BACKUP_PLAN_VOLUME_LAYOUT_UNSUPPORTED' }
    foreach ($mount in @($api.mounts)) {
        if ($mount.Destination.StartsWith('/app/demo-export-state/',[StringComparison]::Ordinal)) { throw 'BACKUP_PLAN_VOLUME_LAYOUT_UNSUPPORTED' }
        if ($mount.Destination -ceq '/run/demo-keys') {
            $keyOnly=[pscustomobject]@{mounts=@($mount)}
            if ((Uses-Source $keyOnly $database) -or (Uses-Source $keyOnly $runtime)) { throw 'BACKUP_PLAN_KEY_SCOPE_INVALID' }
        }
    }
    $participants=@()
    $keySessions=0
    $dbNetworks=@()
    if ($null -ne $db.networks) { foreach ($property in $db.networks.PSObject.Properties) { if ($property.Value.NetworkID -is [string]) { $dbNetworks+=,$property.Value.NetworkID } } }
    foreach ($item in $items) {
        $databaseUser=Uses-Source $item $database
        $runtimeUser=Uses-Source $item $runtime
        $inProject=(Project $item) -ceq 'hybrid-windows'
        if (-not $inProject) {
            if ($databaseUser -or $runtimeUser) { throw 'BACKUP_PLAN_EXTERNAL_VOLUME_CONSUMER' }
            if ($item.running -and $null -ne $item.networks) {
                foreach ($property in $item.networks.PSObject.Properties) { if ($dbNetworks -ccontains $property.Value.NetworkID) { throw 'BACKUP_PLAN_EXTERNAL_DB_NETWORK_CONSUMER' } }
            }
            continue
        }
        if ($item.restarting -or $item.paused -or $item.dead) { throw 'BACKUP_PLAN_UNSTABLE_CONTAINER' }
        $service=Service $item
        if ($service -ceq 'key-session') {
            if ($databaseUser -or $runtimeUser) { throw 'BACKUP_PLAN_KEY_SCOPE_INVALID' }
            $keySessions++
            continue
        }
        if (@('api','db','alerts','web') -cnotcontains $service) { throw 'BACKUP_PLAN_UNKNOWN_PROJECT_SERVICE' }
        $participants+=,[pscustomobject]@{container_id=$item.id;service=$service;running=$item.running;restart_policy=$item.restart.Name;restart_maximum_retry_count=$item.restart.MaximumRetryCount;uses_database_volume=$databaseUser;uses_runtime_volume=$runtimeUser}
    }
    $stage='CONTEXT_RECHECK'
    if ((@(Container-Ids) -join '|') -cne ($ids -join '|')) { throw 'BACKUP_PLAN_CONTEXT_CHANGED' }
    foreach ($item in $items) {
        $fresh=Read-Container $item.id
        if (($item | ConvertTo-Json -Depth 12 -Compress) -cne ($fresh | ConvertTo-Json -Depth 12 -Compress)) { throw 'BACKUP_PLAN_CONTEXT_CHANGED' }
    }
    $contextDigest=Digest ($items | ConvertTo-Json -Depth 12 -Compress)
    [pscustomobject]@{backup_plan='READ_ONLY_COLD_BACKUP_PLAN_OBSERVED_KEEP_PAUSED';source_reference=$configuration.source_reference;metadata_only=$true;automatic_retry=$false;source_context_sha256=$contextDigest;api_container_id=$api.id;api_image_id=$api.image;database_container_id=$db.id;database_image_id=$db.image;postgresql_major=$major;database_mount_destination=$database.Destination;database_volume_identity_sha256=(Digest ($database.Source));runtime_volume_identity_sha256=(Digest ($runtime.Source));database_connection_identity_sha256=(Digest ($properties -join "`n"));participants=$participants;excluded_key_session_containers=$keySessions;key_session_volume_copy_in_scope=$false;local_volume_options_verified=$true;source_database_clean_shutdown_verified=$false;account_pause_rechecked=$false;writer_quiescence_verified=$false;backup_created=$false;restore_tested_on_actual_windows=$false;offline_handoff_performed=$false;backup_ready=$false;installation_ready=$false;source_services_stopped=$false;source_restart_policies_changed=$false;database_changed=$false;files_changed=$false;exchange_credentials_accessed=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;next_action='REVIEW_COLD_STOP_SCOPE_AND_PROTECTED_BACKUP_LOCATION_NO_AUTOMATIC_APPLY'} | ConvertTo-Json -Depth 8 -Compress | Write-Output
    Write-Output 'COLD_BACKUP_PLAN_OBSERVED_KEEP_PAUSED_NO_STOP_NO_BACKUP_NO_ORDERS'
    exit 0
} catch {
    $code='BACKUP_PLAN_RUNNER_FAILED'
    $allowed=@('BACKUP_PLAN_TRANSFER_INVALID','BACKUP_PLAN_DOCKER_READ_FAILED','BACKUP_PLAN_CONTEXT_INVALID','BACKUP_PLAN_UNSTABLE_CONTAINER','BACKUP_PLAN_VOLUME_LAYOUT_UNSUPPORTED','BACKUP_PLAN_VOLUME_OPTIONS_REQUIRE_REVIEW','BACKUP_PLAN_SOURCE_CONTAINER_REQUIRED','BACKUP_PLAN_POSTGRES_LAYOUT_UNSUPPORTED','BACKUP_PLAN_KEY_SCOPE_INVALID','BACKUP_PLAN_EXTERNAL_VOLUME_CONSUMER','BACKUP_PLAN_EXTERNAL_DB_NETWORK_CONSUMER','BACKUP_PLAN_UNKNOWN_PROJECT_SERVICE','BACKUP_PLAN_CONTEXT_CHANGED')
    if ($allowed -ccontains $_.Exception.Message) { $code=$_.Exception.Message }
    [pscustomobject]@{backup_plan='BLOCKED_KEEP_PAUSED';stage=$stage;failure_category=$code;automatic_retry=$false;source_services_stopped=$false;source_restart_policies_changed=$false;database_changed=$false;files_changed=$false;backup_created=$false;backup_ready=$false;installation_ready=$false;actual_http_attempts=0;order_writes=0;exchange_credentials_accessed=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
    exit 2
} finally {
    $OutputEncoding=$oldOutput
    [Console]::OutputEncoding=$oldConsole
}
