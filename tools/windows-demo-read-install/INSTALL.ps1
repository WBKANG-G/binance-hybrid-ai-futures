param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=$PSScriptRoot
$stage='TRANSFER_VERIFICATION'
$apiReplacementStarted=$false
$candidateId=$null
$originalId=$null
$backupName=$null
$originalRestart=$null
$rollback='NOT_NEEDED'
$rollbackVerified=$false
$readInstallConsoleEncoding=[Console]::OutputEncoding
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
function Docker-Checked([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'INSTALL_DOCKER_COMMAND_FAILED' }
}
function Inspect-Api {
    $raw=@(& docker inspect hybrid-windows-api-1 2>$null)
    $code=$LASTEXITCODE
    return Convert-InstallInspection -InspectOutput $raw -InspectExitCode $code
}
function Guard([string]$Id,[string]$Phase,[string]$Expected='') {
    $output=@(& (Join-Path $root 'RUN_GUARD.cmd') $Id $Phase $Expected)
    $code=$LASTEXITCODE
    $output | Write-Host
    if ($code -ne 0) { throw 'INSTALL_GUARD_BLOCKED' }
    $report=($output -join "`n") | ConvertFrom-Json
    if ($report.orders_enabled -ne $false -or $report.live_trading_enabled -ne $false -or $report.execution_approved -ne $false) { throw 'INSTALL_GUARD_BLOCKED' }
    return $report
}
try {
    $manifestPath=Join-Path $root 'TRANSFER_MANIFEST.json'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant() -ne $ManifestSHA256) { throw 'INSTALL_TRANSFER_HASH_MISMATCH' }
    $manifest=Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $path=[IO.Path]::GetFullPath((Join-Path $root $entry.Name))
        if (-not $path.StartsWith(($root+[IO.Path]::DirectorySeparatorChar),[StringComparison]::OrdinalIgnoreCase)) { throw 'INSTALL_TRANSFER_PATH_INVALID' }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -ne $entry.Value) { throw 'INSTALL_TRANSFER_HASH_MISMATCH' }
    }
    Write-Host 'TRANSFER_FILES_VERIFIED'
    . (Join-Path $root 'INSTALL_INSPECTION.ps1')
    . (Join-Path $root 'DOCKER_RECOVERY.ps1')
    $stage='CONTAINER_AND_PERSISTENT_STORAGE_VALIDATION'
    $details=Inspect-Api
    $baseId=Read-InstallProperty $details 'Image'
    if ($baseId -notmatch '^sha256:[0-9a-f]{64}$') { throw 'BASE_IMAGE_ID_INVALID' }
    $originalId=Read-InstallProperty $details 'Id'
    $storageMount=@(Read-InstallProperty $details 'Mounts' | Where-Object { (Read-InstallProperty $_ 'Destination') -eq '/app/demo-export-state' -and @('volume','bind') -contains (Read-InstallProperty $_ 'Type') -and (Read-InstallProperty $_ 'RW') -eq $true })
    if ($storageMount.Count -ne 1) { throw 'INSTALL_PERSISTENT_STORAGE_REQUIRED' }
    $reference=Get-Content -Raw -LiteralPath (Join-Path $root 'SOURCE_REFERENCE.json') | ConvertFrom-Json
    $beforeTag='hybrid-read-before:'+$baseId.Substring(7,12)
    $targetTag='hybrid-read-code:'+$reference.candidate_reference.Substring(0,12)+'-'+$baseId.Substring(7,12)
    $plan=Get-RecoveryCreatePlan -Details $details -TargetImage $targetTag
    $planHash=Get-RecoveryPlanDigest $plan
    $policy=$plan.HostConfig.RestartPolicy
    if (@('no','always','unless-stopped','on-failure') -notcontains $policy.Name) { throw 'INSTALL_RESTART_POLICY_UNSUPPORTED' }
    $originalRestart=$policy.Name
    if ($policy.Name -eq 'on-failure' -and $policy.MaximumRetryCount -gt 0) { $originalRestart+=':'+([string]$policy.MaximumRetryCount) }
    $stage='PRECHECK'
    $pre=Guard $originalId 'pre'
    if ($pre.read_code_install -ne 'PRECHECKED_READ_CODE_INSTALL_KEEP_PAUSED') { throw 'INSTALL_GUARD_BLOCKED' }
    if ($pre.target_already_installed -eq $true) {
        $null=Guard $originalId 'post' $pre.preserved_snapshot_sha256
        Write-Host 'READ_CODE_ALREADY_INSTALLED_VERIFIED_KEEP_PAUSED_NO_ORDERS'
        exit 0
    }
    $stage='LOCAL_IMAGE_BUILD'
    Docker-Checked @('tag',$baseId,$beforeTag)
    Docker-Checked @('build','--pull=false','--network','none','--build-arg',('BASE_IMAGE='+$beforeTag),'-t',$targetTag,'-f',(Join-Path $root 'Dockerfile'),$root)
    $stage='FINAL_PRECHECK'
    $fresh=Inspect-Api
    if ((Read-InstallProperty $fresh 'Id') -ne $originalId -or (Read-InstallProperty $fresh 'Image') -ne $baseId -or (Get-RecoveryPlanDigest (Get-RecoveryCreatePlan $fresh $targetTag)) -ne $planHash) { throw 'INSTALL_CONTAINER_CHANGED' }
    $null=Guard $originalId 'pre' $pre.preserved_snapshot_sha256
    $suffix=[Guid]::NewGuid().ToString('N').Substring(0,12)
    $backupName='hybrid-windows-api-read-backup-'+$suffix
    $candidateName='hybrid-windows-api-read-candidate-'+$suffix
    $stage='CANDIDATE_CREATION'
    $createPlan=Get-RecoveryJsonCopy $plan
    $createPlan.HostConfig.RestartPolicy.Name='no'
    $createPlan.HostConfig.RestartPolicy.MaximumRetryCount=0
    $candidateId=New-PreservedRecoveryContainer -Plan $createPlan -Name $candidateName
    $fresh=Inspect-Api
    if ((Read-InstallProperty $fresh 'Id') -ne $originalId -or (Get-RecoveryPlanDigest (Get-RecoveryCreatePlan $fresh $targetTag)) -ne $planHash) { throw 'INSTALL_CONTAINER_CHANGED' }
    $null=Guard $originalId 'pre' $pre.preserved_snapshot_sha256
    @{original_container_id=$originalId;backup_container=$backupName;candidate_container_id=$candidateId;original_image=$baseId;target_image=$targetTag;original_restart_policy=$originalRestart;preserved_snapshot_sha256=$pre.preserved_snapshot_sha256} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'INSTALL_STATE.json') -Encoding ASCII
    $stage='API_REPLACEMENT'
    $apiReplacementStarted=$true
    Docker-Checked @('stop','--time','10',$originalId)
    # A retained backup must not start alongside the new API on daemon restart.
    Docker-Checked @('update','--restart=no',$originalId)
    Docker-Checked @('update',('--restart='+$originalRestart),$candidateId)
    Docker-Checked @('rename',$originalId,$backupName)
    Docker-Checked @('rename',$candidateId,'hybrid-windows-api-1')
    Docker-Checked @('start',$candidateId)
    $stage='API_HEALTH_CHECK'
    $watch=[Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        $current=Read-RecoveryContainer $candidateId
        $state=Read-InstallProperty $current 'State'
        $health=Read-InstallProperty (Read-InstallProperty $state 'Health') 'Status'
        if ((Read-InstallProperty $state 'Running') -eq $true -and $health -eq 'healthy') { break }
        if ((Read-InstallProperty $state 'Running') -ne $true -or $health -ne 'starting' -or $watch.Elapsed.TotalSeconds -gt 90) { throw 'INSTALL_NEW_API_NOT_HEALTHY' }
        Start-Sleep -Seconds 2
    }
    $stage='CONFIGURATION_POSTCHECK'
    $current=Inspect-Api
    if ((Read-InstallProperty $current 'Id') -ne $candidateId -or (Get-RecoveryPlanDigest (Get-RecoveryCreatePlan $current $targetTag)) -ne $planHash) { throw 'INSTALL_CONFIGURATION_CHANGED' }
    $stage='SOURCE_AND_DATA_POSTCHECK'
    $post=Guard $candidateId 'post' $pre.preserved_snapshot_sha256
    if ($post.read_code_install -ne 'POSTCHECKED_READ_CODE_INSTALLED_KEEP_PAUSED' -or $post.installed_command_verified -ne $true) { throw 'INSTALL_GUARD_BLOCKED' }
    [pscustomobject]@{read_code_install='INSTALLED_VERIFIED_KEEP_PAUSED';api_replacement_started=$true;backup_container=$backupName;backup_restart_disabled=$true;installed_command_verified=$true;preserved_snapshot_sha256=$post.preserved_snapshot_sha256;exchange_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;next_action='KEEP_PAUSED_NO_EXCHANGE_READ_OR_ORDER_RETRY'} | ConvertTo-Json -Compress | Write-Host
    Write-Host 'READ_CODE_IMAGE_INSTALLED_AUDIT_VERIFIED_KEEP_PAUSED_NO_ORDERS'
    exit 0
} catch {
    $reason='READ_CODE_INSTALL_UNAVAILABLE'
    if ($_.Exception.Message -match '^(INSTALL|RECOVERY|CONTAINER|API|BASE_IMAGE|COMPOSE)_[A-Z0-9_]+$') { $reason=$_.Exception.Message }
    if ($apiReplacementStarted -and $null -ne $candidateId) {
        $rollback='RESTORE_FAILED_REVIEW_REQUIRED'
        try {
            Docker-Checked @('update','--restart=no',$candidateId)
            Docker-Checked @('update',('--restart='+$originalRestart),$originalId)
            $failedName='hybrid-windows-api-read-failed-'+[Guid]::NewGuid().ToString('N').Substring(0,12)
            $null=Restore-PreservedRecoveryContainer $originalId $candidateId 'hybrid-windows-api-1' $failedName
            $rollback='ORIGINAL_API_RESTORED'
            $null=Guard $originalId 'pre' $pre.preserved_snapshot_sha256
            $rollbackVerified=$true
        } catch { }
    }
    [pscustomobject]@{read_code_install='BLOCKED_KEEP_PAUSED';stage=$stage;stop_reason=$reason;api_replacement_started=$apiReplacementStarted;backup_container=$backupName;rollback_status=$rollback;rollback_data_verified=$rollbackVerified;exchange_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;next_action='SHARE_OUTPUT_NO_AUTOMATIC_RETRY'} | ConvertTo-Json -Compress | Write-Host
    exit 1
} finally {
    [Console]::OutputEncoding=$readInstallConsoleEncoding
}
