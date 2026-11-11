$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$container = 'hybrid-windows-api-1'
$stage = 'TRANSFER_VERIFICATION'
$root = $PSScriptRoot
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
    $stage = 'CONTAINER_INSPECTION'
    $details = (& docker inspect $container | ConvertFrom-Json)[0]
    if ($LASTEXITCODE -ne 0 -or -not $details.State.Running) { throw 'API_CONTAINER_NOT_RUNNING' }
    if ($details.State.Health.Status -ne 'healthy') { throw 'API_CONTAINER_NOT_HEALTHY' }
    $labels = $details.Config.Labels
    $project = $labels.'com.docker.compose.project'
    $service = $labels.'com.docker.compose.service'
    $directory = $labels.'com.docker.compose.project.working_dir'
    $files = @($labels.'com.docker.compose.project.config_files' -split ',')
    if ($project -ne 'hybrid-windows' -or $service -ne 'api' -or -not (Test-Path -LiteralPath $directory -PathType Container)) {
        throw 'ORIGINAL_COMPOSE_PROJECT_UNAVAILABLE'
    }
    foreach ($file in $files) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'ORIGINAL_COMPOSE_FILE_UNAVAILABLE' }
    }
    $stage = 'COMPOSE_CONFIGURATION'
    $baseId = [string]$details.Image
    if ($baseId -notmatch '^sha256:[0-9a-f]{64}$') { throw 'BASE_IMAGE_ID_INVALID' }
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
    $current = (& docker inspect --format '{{.Image}}' $container) -join ''
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
    Invoke-Docker -DockerArgs $composeArgs
    $stage = 'POSTCHECK'
    $post = Read-Guard -GuardArgs @('--phase','post','--expected-state',$pre.state_snapshot_sha256)
    if ($post.runtime_code_install -ne 'POSTCHECKED_CODE_ONLY_KEEP_PAUSED') { throw 'POSTCHECK_STATUS_INVALID' }
    Write-Host 'CODE_ONLY_INSTALL_VERIFIED_KEEP_PAUSED_STORAGE_NOT_PROVISIONED'
    exit 0
} catch {
    # Exceptions can contain host paths or environment details: print a fixed code.
    Write-Host ('INSTALL_STOPPED_STAGE_' + $stage + '_KEEP_PAUSED_NO_AUTOMATIC_RETRY')
    exit 2
}
