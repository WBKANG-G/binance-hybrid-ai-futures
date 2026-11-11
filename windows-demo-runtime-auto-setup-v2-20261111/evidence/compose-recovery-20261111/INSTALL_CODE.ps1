$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$container = 'hybrid-windows-api-1'
$stage = 'TRANSFER_VERIFICATION'
$root = $PSScriptRoot
$apiReplacementStarted = $false
$recovery = $false
$candidateId = $null
$backupName = $null
$originalStopped = $false
$rollbackStatus = 'NOT_NEEDED'
$rollbackDatabaseVerified = $false
$failedName = $null
function Invoke-Docker([string[]]$DockerArgs) {
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) { throw 'DOCKER_COMMAND_FAILED_KEEP_PAUSED' }
}
function Read-Guard([string[]]$GuardArgs) {
    $source = Get-Content -Raw -LiteralPath (Join-Path $root 'guard_runtime_code_install.py')
    $output = $source | & docker exec -i $container python - @GuardArgs
    if ($LASTEXITCODE -ne 0) {
        $output | Write-Host
        throw 'INSTALL_GUARD_BLOCKED_KEEP_PAUSED'
    }
    $result = ($output -join "`n") | ConvertFrom-Json
    $output | Write-Host
    return $result
}
try {
    $manifest = Get-Content -Raw -LiteralPath (Join-Path $root 'TRANSFER_MANIFEST.json') | ConvertFrom-Json
    foreach ($item in $manifest.files.PSObject.Properties) {
        $path = [IO.Path]::GetFullPath((Join-Path $root $item.Name))
        if (-not $path.StartsWith(($root + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) {
            throw 'TRANSFER_PATH_INVALID'
        }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() -ne $item.Value) {
            throw 'TRANSFER_FILE_HASH_MISMATCH'
        }
    }
    Write-Host 'TRANSFER_FILES_VERIFIED'
    . (Join-Path $root 'INSTALL_INSPECTION.ps1')
    . (Join-Path $root 'DOCKER_RECOVERY.ps1')
    $stage = 'CONTAINER_INSPECT_COMMAND'
    $inspectOutput = @(& docker inspect $container 2>$null)
    $inspectExitCode = $LASTEXITCODE
    $stage = 'CONTAINER_INSPECT_JSON'
    $details = Convert-InstallInspection -InspectOutput $inspectOutput -InspectExitCode $inspectExitCode
    Get-SafeInstallInspection $details | ConvertTo-Json -Compress | Write-Host
    $stage = 'CONTAINER_AND_COMPOSE_VALIDATION'
    try {
        $context = Get-InstallComposeContext $details
        $project = $context.Project
        $service = $context.Service
        $directory = $context.Directory
        $files = @($context.Files)
        $baseId = $context.BaseId
    } catch {
        if (@('COMPOSE_WORKING_DIRECTORY_NOT_FOUND','COMPOSE_CONFIG_FILE_NOT_FOUND') -notcontains $_.Exception.Message) { throw }
        $recovery = $true
        $baseId = [string](Read-InstallProperty $details 'Image')
        if ($baseId -notmatch '^sha256:[0-9a-f]{64}$') { throw 'BASE_IMAGE_ID_INVALID' }
        $null = Get-RecoveryCreatePlan -Details $details -TargetImage $baseId
        Write-Host 'COMPOSE_FILES_MISSING_PRESERVE_CURRENT_CONTAINER_CONFIGURATION'
    }
    $tail = $baseId.Substring(7,12)
    $beforeTag = 'hybrid-windows-runtime-before:20261111-' + $tail
    $targetTag = 'hybrid-windows-runtime-controls:20261111-' + $tail
    # Keep the current image addressable locally; no images or volumes are deleted.
    Invoke-Docker -DockerArgs @('tag',$baseId,$beforeTag)
    $stage = 'PRECHECK'
    $pre = Read-Guard -GuardArgs @('--phase','pre')
    if ($pre.runtime_code_install -ne 'PRECHECKED_CODE_ONLY_KEEP_PAUSED') { throw 'PRECHECK_STATUS_INVALID' }
    $stage = 'IMAGE_BUILD'
    Invoke-Docker -DockerArgs @('build','--pull=false','--network','none','--build-arg',('BASE_IMAGE=' + $beforeTag),'-t',$targetTag,'-f',(Join-Path $root 'Dockerfile'),$root)
    # Recheck after the build and immediately before replacing the API container.
    $current = (& docker inspect --format '{{.Image}}' $container 2>$null) -join ''
    if ($LASTEXITCODE -ne 0 -or $current -ne $baseId) { throw 'API_IMAGE_CHANGED_DURING_BUILD' }
    $stage = 'FINAL_PRECHECK'
    $finalPre = Read-Guard -GuardArgs @('--phase','pre')
    if ($finalPre.state_snapshot_sha256 -ne $pre.state_snapshot_sha256) { throw 'DATABASE_CHANGED_DURING_BUILD' }
    if ($recovery) {
        $stage = 'RECOVERY_CANDIDATE_CREATION'
        $suffix = [Guid]::NewGuid().ToString('N').Substring(0,12)
        $candidateName = 'hybrid-windows-api-candidate-' + $suffix
        $backupName = 'hybrid-windows-api-backup-' + $suffix
        $plan = Get-RecoveryCreatePlan -Details $details -TargetImage $targetTag
        $planHash = Get-RecoveryPlanDigest $plan
        $candidateId = New-PreservedRecoveryContainer -Plan $plan -Name $candidateName
        $freshOutput = @(& docker inspect $container 2>$null)
        $freshCode = $LASTEXITCODE
        $fresh = Convert-InstallInspection -InspectOutput $freshOutput -InspectExitCode $freshCode
        $freshPlan = Get-RecoveryCreatePlan -Details $fresh -TargetImage $targetTag
        if ((Read-InstallProperty $fresh 'Id') -ne (Read-InstallProperty $details 'Id') -or
            (Get-RecoveryPlanDigest $freshPlan) -ne $planHash) { throw 'RECOVERY_CONFIG_CHANGED_BEFORE_REPLACE' }
        $lastPre = Read-Guard -GuardArgs @('--phase','pre')
        if ($lastPre.state_snapshot_sha256 -ne $pre.state_snapshot_sha256) { throw 'DATABASE_CHANGED_DURING_BUILD' }
        @{original_image=$baseId;backup_image=$beforeTag;installed_image=$targetTag;
            original_container_id=(Read-InstallProperty $details 'Id');backup_container=$backupName;
            candidate_container_id=$candidateId;state_snapshot_sha256=$pre.state_snapshot_sha256;
            deployment='PRESERVED_DOCKER_CONFIGURATION'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'INSTALL_STATE.json') -Encoding ASCII
        $stage = 'RECOVERY_API_REPLACEMENT'
        $apiReplacementStarted = $true
        Invoke-Docker -DockerArgs @('stop','--time','10',(Read-InstallProperty $details 'Id'))
        $originalStopped = $true
        Invoke-Docker -DockerArgs @('rename',(Read-InstallProperty $details 'Id'),$backupName)
        Invoke-Docker -DockerArgs @('rename',$candidateId,$container)
        Invoke-Docker -DockerArgs @('start',$container)
        $watch = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            $health = (& docker inspect --format '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $container 2>$null) -join ''
            if ($LASTEXITCODE -ne 0) { throw 'RECOVERY_NEW_CONTAINER_UNAVAILABLE' }
            if ($health -eq 'true|healthy') { break }
            if ($health -ne 'true|starting' -or $watch.Elapsed.TotalSeconds -gt 90) { throw 'RECOVERY_NEW_CONTAINER_NOT_HEALTHY' }
            Start-Sleep -Seconds 2
        }
        $stage = 'RECOVERY_CONFIGURATION_POSTCHECK'
        $newOutput = @(& docker inspect $container 2>$null)
        $newCode = $LASTEXITCODE
        $newDetails = Convert-InstallInspection -InspectOutput $newOutput -InspectExitCode $newCode
        if ((Get-RecoveryPlanDigest (Get-RecoveryCreatePlan -Details $newDetails -TargetImage $targetTag)) -ne $planHash) {
            throw 'RECOVERY_CONFIGURATION_POSTCHECK_FAILED'
        }
        Write-Host 'PRESERVED_CONTAINER_CONFIGURATION_VERIFIED_BACKUP_RETAINED'
    } else {
        $overridePath = Join-Path $root 'runtime-image.override.json'
        @{services=@{api=@{image=$targetTag;pull_policy='never';environment=@{LIVE_TRADING_ENABLED='false';TRADING_MODE='PAPER'}}}} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $overridePath -Encoding ASCII
        @{original_image=$baseId;backup_image=$beforeTag;installed_image=$targetTag;state_snapshot_sha256=$pre.state_snapshot_sha256} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'INSTALL_STATE.json') -Encoding ASCII
        $composeArgs = @('compose','--project-directory',$directory,'--project-name',$project)
        foreach ($file in $files) { $composeArgs += @('-f',$file) }
        $composeArgs += @('-f',$overridePath,'up','-d','--no-deps','--no-build','--pull','never','--wait','--wait-timeout','90','api')
        $stage = 'API_REPLACEMENT'
        $apiReplacementStarted = $true
        Invoke-Docker -DockerArgs $composeArgs
    }
    $stage = 'POSTCHECK'
    $post = Read-Guard -GuardArgs @('--phase','post','--expected-state',$pre.state_snapshot_sha256)
    if ($post.runtime_code_install -ne 'POSTCHECKED_CODE_ONLY_KEEP_PAUSED') { throw 'POSTCHECK_STATUS_INVALID' }
    Write-Host 'CODE_ONLY_INSTALL_VERIFIED_KEEP_PAUSED_STORAGE_NOT_PROVISIONED'
    exit 0
} catch {
    $allowed = @('TRANSFER_PATH_INVALID','TRANSFER_FILE_HASH_MISMATCH',
        'CONTAINER_INSPECT_COMMAND_FAILED','CONTAINER_INSPECT_JSON_INVALID','CONTAINER_INSPECT_SHAPE_INVALID',
        'CONTAINER_IDENTITY_MISMATCH','API_CONTAINER_NOT_RUNNING','API_HEALTHCHECK_MISSING',
        'API_CONTAINER_HEALTH_STARTING','API_CONTAINER_NOT_HEALTHY','COMPOSE_LABELS_MISSING',
        'COMPOSE_PROJECT_LABEL_MISMATCH','COMPOSE_SERVICE_LABEL_MISMATCH',
        'COMPOSE_WORKING_DIRECTORY_LABEL_MISSING','COMPOSE_WORKING_DIRECTORY_NOT_FOUND',
        'COMPOSE_CONFIG_FILES_LABEL_MISSING','COMPOSE_CONFIG_FILES_INVALID','COMPOSE_CONFIG_FILE_NOT_FOUND',
        'BASE_IMAGE_ID_INVALID','DOCKER_COMMAND_FAILED_KEEP_PAUSED','INSTALL_GUARD_BLOCKED_KEEP_PAUSED',
        'PRECHECK_STATUS_INVALID','API_IMAGE_CHANGED_DURING_BUILD','DATABASE_CHANGED_DURING_BUILD',
        'POSTCHECK_STATUS_INVALID')
    $allowed += @('RECOVERY_IDENTITY_MISMATCH','RECOVERY_COMPOSE_IDENTITY_MISMATCH','RECOVERY_USER_UNSUPPORTED','RECOVERY_WORKDIR_UNSUPPORTED','RECOVERY_HOST_OPTIONS_UNSUPPORTED','RECOVERY_VOLUME_INHERITANCE_UNSUPPORTED','RECOVERY_NETWORK_MODE_UNSUPPORTED','RECOVERY_STATIC_NETWORK_UNSUPPORTED','RECOVERY_DYNAMIC_PORT_UNSUPPORTED','RECOVERY_MOUNT_SHADOWS_CODE','RECOVERY_MOUNT_TYPE_UNSUPPORTED','RECOVERY_UNREPRESENTED_VOLUME','RECOVERY_ENGINE_RESPONSE_INVALID','RECOVERY_ENGINE_REQUEST_REJECTED','RECOVERY_ENGINE_ROUTE_FORBIDDEN','RECOVERY_ENGINE_CONNECTION_FAILED','RECOVERY_ENGINE_TIMEOUT','RECOVERY_ENGINE_VERSION_INVALID','RECOVERY_CANDIDATE_NAME_INVALID','RECOVERY_CREATED_ID_INVALID','RECOVERY_CONFIG_CHANGED_BEFORE_REPLACE','RECOVERY_NEW_CONTAINER_UNAVAILABLE','RECOVERY_NEW_CONTAINER_NOT_HEALTHY','RECOVERY_CONFIGURATION_POSTCHECK_FAILED')
    $reason = 'INSTALLER_UNEXPECTED_ERROR'
    if ($allowed -contains $_.Exception.Message) { $reason = $_.Exception.Message }
    elseif ($_.FullyQualifiedErrorId -like '*PropertyNotFound*') { $reason = 'INSTALLER_PROPERTY_ACCESS_FAILED' }
    if ($recovery -and $apiReplacementStarted -and $null -ne $candidateId) {
        $rollbackStatus = 'RESTORE_FAILED_REVIEW_REQUIRED'
        $failedName = 'hybrid-windows-api-failed-' + [Guid]::NewGuid().ToString('N').Substring(0,12)
        try {
            $restored = Restore-PreservedRecoveryContainer -OriginalId (Read-InstallProperty $details 'Id') -CandidateId $candidateId -ContainerName $container -FailedName $failedName
            if ($restored) {
                $rollbackStatus = 'ORIGINAL_API_RESTORED'
                try {
                    $rollbackPre = Read-Guard -GuardArgs @('--phase','pre')
                    $rollbackDatabaseVerified = ($rollbackPre.state_snapshot_sha256 -eq $pre.state_snapshot_sha256)
                } catch { $rollbackDatabaseVerified = $false }
            }
        } catch { $rollbackStatus = 'RESTORE_FAILED_REVIEW_REQUIRED' }
    } elseif (-not $apiReplacementStarted -and $null -ne $candidateId) {
        try { & docker rm $candidateId 1>$null 2>$null } catch { }
    }
    [pscustomobject]@{runtime_code_install='BLOCKED_KEEP_PAUSED'; stage=$stage; stop_reason=$reason;
        api_replacement_started=$apiReplacementStarted; backup_container=$backupName; rollback_status=$rollbackStatus; rollback_database_verified=$rollbackDatabaseVerified; failed_container=$failedName; execution_approved=$false;
        orders_enabled=$false; live_trading_enabled=$false;
        next_action='SHARE_OUTPUT_NO_AUTOMATIC_RETRY'} | ConvertTo-Json -Compress | Write-Host
    exit 2
}
