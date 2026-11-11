$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$container = 'hybrid-windows-api-1'
$stage = 'TRANSFER_VERIFICATION'
$root = $PSScriptRoot
$apiReplacementStarted = $false
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
    $stage = 'CONTAINER_INSPECT_COMMAND'
    $inspectOutput = @(& docker inspect $container 2>$null)
    $inspectExitCode = $LASTEXITCODE
    $stage = 'CONTAINER_INSPECT_JSON'
    $details = Convert-InstallInspection -InspectOutput $inspectOutput -InspectExitCode $inspectExitCode
    Get-SafeInstallInspection $details | ConvertTo-Json -Compress | Write-Host
    $stage = 'CONTAINER_AND_COMPOSE_VALIDATION'
    $context = Get-InstallComposeContext $details
    $project = $context.Project
    $service = $context.Service
    $directory = $context.Directory
    $files = @($context.Files)
    $baseId = $context.BaseId
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
    $overridePath = Join-Path $root 'runtime-image.override.json'
    @{services=@{api=@{image=$targetTag;pull_policy='never';environment=@{LIVE_TRADING_ENABLED='false';TRADING_MODE='PAPER'}}}} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $overridePath -Encoding ASCII
    @{original_image=$baseId;backup_image=$beforeTag;installed_image=$targetTag;state_snapshot_sha256=$pre.state_snapshot_sha256} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'INSTALL_STATE.json') -Encoding ASCII
    $composeArgs = @('compose','--project-directory',$directory,'--project-name',$project)
    foreach ($file in $files) { $composeArgs += @('-f',$file) }
    $composeArgs += @('-f',$overridePath,'up','-d','--no-deps','--no-build','--pull','never','--wait','--wait-timeout','90','api')
    $stage = 'API_REPLACEMENT'
    $apiReplacementStarted = $true
    Invoke-Docker -DockerArgs $composeArgs
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
    $reason = 'INSTALLER_UNEXPECTED_ERROR'
    if ($allowed -contains $_.Exception.Message) { $reason = $_.Exception.Message }
    elseif ($_.FullyQualifiedErrorId -like '*PropertyNotFound*') { $reason = 'INSTALLER_PROPERTY_ACCESS_FAILED' }
    [pscustomobject]@{runtime_code_install='BLOCKED_KEEP_PAUSED'; stage=$stage; stop_reason=$reason;
        api_replacement_started=$apiReplacementStarted; execution_approved=$false;
        orders_enabled=$false; live_trading_enabled=$false;
        next_action='SHARE_OUTPUT_NO_AUTOMATIC_RETRY'} | ConvertTo-Json -Compress | Write-Host
    exit 2
}
