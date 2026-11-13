param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=$PSScriptRoot
$stage='TRANSFER_VERIFICATION'
$oldOutput=$OutputEncoding
$oldConsole=[Console]::OutputEncoding
$OutputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
function Fail-Preflight([string]$Code) {
    [pscustomobject]@{backup_scope_preflight='BLOCKED_KEEP_PAUSED';stage=$stage;failure_category=$Code;automatic_retry=$false;database_changed=$false;files_changed=$false;backup_created=$false;restore_tested=$false;api_replaced=$false;writers_stopped=$false;writer_quiescence_verified=$false;exchange_credentials_accessed=$false;credential_session_opened=$false;journal_opened=$false;clock_recovery_attempted=$false;offline_copy_performed=$false;reference_generated=$false;backup_ready=$false;installation_ready=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
}
function Assert-PreflightValue([object]$Value,[object]$Schema) {
    if ($null -eq $Value) {
        if ($Schema.nullable -eq $true) { return }
        throw 'PREFLIGHT_OUTPUT_INVALID'
    }
    switch -CaseSensitive ($Schema.kind) {
        'object' {
            if ($Value -isnot [pscustomobject]) { throw 'PREFLIGHT_OUTPUT_INVALID' }
            $names=@($Value.PSObject.Properties.Name | Sort-Object)
            $expected=@($Schema.fields.PSObject.Properties.Name | Sort-Object)
            if (($names -join '|') -cne ($expected -join '|')) { throw 'PREFLIGHT_OUTPUT_INVALID' }
            foreach ($field in $Schema.fields.PSObject.Properties) { Assert-PreflightValue $Value.($field.Name) $field.Value }
        }
        'array' {
            if ($Value -isnot [System.Array] -or $Value.Count -gt $Schema.maximum) { throw 'PREFLIGHT_OUTPUT_INVALID' }
            foreach ($entry in $Value) { Assert-PreflightValue $entry $Schema.item }
        }
        'boolean' {
            if ($Value -isnot [bool]) { throw 'PREFLIGHT_OUTPUT_INVALID' }
            if ($null -ne $Schema.value -and $Value -ne $Schema.value) { throw 'PREFLIGHT_OUTPUT_INVALID' }
        }
        'integer' {
            if (($Value -isnot [int] -and $Value -isnot [long]) -or $Value -lt $Schema.minimum -or $Value -gt $Schema.maximum) { throw 'PREFLIGHT_OUTPUT_INVALID' }
        }
        'string' {
            if ($Value -isnot [string] -or $Value.Length -gt 256) { throw 'PREFLIGHT_OUTPUT_INVALID' }
            if ($Schema.values.Count -gt 0 -and $Schema.values -cnotcontains $Value) { throw 'PREFLIGHT_OUTPUT_INVALID' }
            if ($null -ne $Schema.pattern -and $Value -cnotmatch $Schema.pattern) { throw 'PREFLIGHT_OUTPUT_INVALID' }
        }
        default { throw 'PREFLIGHT_OUTPUT_INVALID' }
    }
}
function Read-PreflightDockerField([string]$Identity,[string]$Format) {
    $lines=@(& docker inspect --format $Format $Identity 2>$null)
    if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 32768) { throw 'PREFLIGHT_CONTAINER_UNAVAILABLE' }
    return [string]$lines[0]
}
function Get-PreflightDigest([string]$Text) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Read-PreflightContext([string]$Id) {
    if ((Read-PreflightDockerField $Id '{{.Name}}') -cne '/hybrid-windows-api-1' -or (Read-PreflightDockerField $Id '{{.State.Running}}') -cne 'true' -or (Read-PreflightDockerField $Id '{{.Config.WorkingDir}}') -cne '/app') { throw 'PREFLIGHT_CONTEXT_INVALID' }
    if (@('10001','10001:10001') -cnotcontains (Read-PreflightDockerField $Id '{{.Config.User}}')) { throw 'PREFLIGHT_CONTEXT_INVALID' }
    $labels=Read-PreflightDockerField $Id '{{json .Config.Labels}}' | ConvertFrom-Json
    if ($labels -isnot [pscustomobject] -or $labels.'com.docker.compose.project' -cne 'hybrid-windows' -or $labels.'com.docker.compose.service' -cne 'api') { throw 'PREFLIGHT_CONTEXT_INVALID' }
    $image=Read-PreflightDockerField $Id '{{.Image}}'
    if ($image -cnotmatch '^sha256:[0-9a-f]{64}$') { throw 'PREFLIGHT_CONTEXT_INVALID' }
    $mounts=Read-PreflightDockerField $Id '{{json .Mounts}}' | ConvertFrom-Json
    if ($null -eq $mounts -or @($mounts).Count -gt 32) { throw 'PREFLIGHT_MOUNT_INVALID' }
    $selected=@()
    foreach ($mount in @($mounts)) {
        if ($mount -isnot [pscustomobject] -or $mount.Destination -isnot [string] -or $mount.Destination.Length -gt 1024) { throw 'PREFLIGHT_MOUNT_INVALID' }
        if ($mount.Destination.StartsWith('/app/demo-export-state/',[StringComparison]::Ordinal)) { throw 'PREFLIGHT_NESTED_MOUNT_REQUIRES_REVIEW' }
        if ($mount.Destination -ceq '/app/demo-export-state') { $selected+=,$mount }
    }
    if ($selected.Count -ne 1) { throw 'PREFLIGHT_MOUNT_INVALID' }
    $mount=$selected[0]
    if (@('bind','volume') -cnotcontains $mount.Type -or $mount.RW -isnot [bool] -or $mount.RW -ne $true -or $mount.Source -isnot [string] -or $mount.Source.Length -eq 0 -or $mount.Source.Length -gt 4096) { throw 'PREFLIGHT_MOUNT_INVALID' }
    # Host source paths and volume names stay in memory; only a digest is displayed.
    $identity=[ordered]@{type=$mount.Type;source=$mount.Source;destination=$mount.Destination;rw=$mount.RW}
    $digest=Get-PreflightDigest ($identity | ConvertTo-Json -Compress)
    return [pscustomobject]@{container_id=$Id;image_id=$image;storage_destination='/app/demo-export-state';storage_mount_type=$mount.Type;storage_read_write=$true;storage_mount_identity_sha256=$digest;nested_storage_mounts=0}
}
try {
    if ($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$') { throw 'PREFLIGHT_TRANSFER_INVALID' }
    $manifestPath=Join-Path $root 'TRANSFER_MANIFEST.json'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant() -cne $ManifestSHA256) { throw 'PREFLIGHT_TRANSFER_INVALID' }
    $manifest=Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    $required=@('worker.py','PREFLIGHT.ps1','README_KO.md','PREFLIGHT_CONFIG.json','REPORT_SCHEMA.json')
    if ((@($manifest.files.PSObject.Properties.Name | Sort-Object) -join '|') -cne (@($required | Sort-Object) -join '|')) { throw 'PREFLIGHT_TRANSFER_INVALID' }
    foreach ($entry in $manifest.files.PSObject.Properties) {
        if ($entry.Value -isnot [string] -or $entry.Value -cnotmatch '^[0-9a-f]{64}$') { throw 'PREFLIGHT_TRANSFER_INVALID' }
        $path=Join-Path $root $entry.Name
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -cne $entry.Value) { throw 'PREFLIGHT_TRANSFER_INVALID' }
    }
    Write-Output 'TRANSFER_FILES_VERIFIED'
    $configuration=Get-Content -Raw -LiteralPath (Join-Path $root 'PREFLIGHT_CONFIG.json') | ConvertFrom-Json
    $schema=Get-Content -Raw -LiteralPath (Join-Path $root 'REPORT_SCHEMA.json') | ConvertFrom-Json
    $stage='CONTAINER_AND_MOUNT_IDENTITY'
    $id=Read-PreflightDockerField 'hybrid-windows-api-1' '{{.Id}}'
    if ($id -cnotmatch '^[0-9a-f]{64}$') { throw 'PREFLIGHT_CONTAINER_UNAVAILABLE' }
    $before=Read-PreflightContext $id
    $stage='READ_ONLY_WORKER'
    $worker=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'worker.py')))
    $config=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'PREFLIGHT_CONFIG.json')))
    $payload="import base64,json`nCONFIG=json.loads(base64.b64decode('$config'))`nexec(compile(base64.b64decode('$worker'),'<backup-scope-preflight>','exec'))"
    $output=@($payload | & docker exec -i $id python -I -B - 2>$null)
    $code=$LASTEXITCODE
    $joined=$output -join "`n"
    if ($joined.Length -gt 49152) { throw 'PREFLIGHT_OUTPUT_INVALID' }
    $result=ConvertFrom-Json -InputObject $joined
    Assert-PreflightValue $result $schema
    if ($result.source_reference -cne $configuration.source_reference) { throw 'PREFLIGHT_OUTPUT_INVALID' }
    if (@(0,2) -notcontains $code) { throw 'PREFLIGHT_OUTPUT_INVALID' }
    if ($code -eq 0) {
        if ($result.backup_scope_preflight -cne 'READ_ONLY_SCOPE_OBSERVED_KEEP_PAUSED' -or $result.stage -cne 'COMPLETE' -or $result.failure_category -cne 'NONE' -or $null -ne $result.sqlstate -or $result.account_found -ne $true -or $result.account_paused -ne $true -or $result.database_configuration_used -ne $true -or $null -eq $result.database_scope -or $null -eq $result.catalog -or $null -eq $result.storage) { throw 'PREFLIGHT_OUTPUT_INVALID' }
    } elseif ($result.backup_scope_preflight -cne 'BLOCKED_KEEP_PAUSED' -or $result.failure_category -ceq 'NONE') { throw 'PREFLIGHT_OUTPUT_INVALID' }
    $stage='CONTEXT_RECHECK'
    if ((Read-PreflightDockerField 'hybrid-windows-api-1' '{{.Id}}') -cne $id) { throw 'PREFLIGHT_CONTEXT_CHANGED' }
    $after=Read-PreflightContext $id
    if (($before | ConvertTo-Json -Compress) -cne ($after | ConvertTo-Json -Compress)) { throw 'PREFLIGHT_CONTEXT_CHANGED' }
    [pscustomobject]@{container_context=$after;worker_report=$result} | ConvertTo-Json -Depth 12 -Compress | Write-Output
    if ($code -ne 0) { exit 2 }
    Write-Output 'BACKUP_SCOPE_OBSERVED_KEEP_PAUSED_NO_BACKUP_NO_ORDERS'
    exit 0
} catch {
    $reason='PREFLIGHT_RUNNER_FAILED'
    $allowed=@('PREFLIGHT_TRANSFER_INVALID','PREFLIGHT_CONTAINER_UNAVAILABLE','PREFLIGHT_CONTEXT_INVALID','PREFLIGHT_MOUNT_INVALID','PREFLIGHT_NESTED_MOUNT_REQUIRES_REVIEW','PREFLIGHT_OUTPUT_INVALID','PREFLIGHT_CONTEXT_CHANGED')
    if ($allowed -ccontains $_.Exception.Message) { $reason=$_.Exception.Message }
    Fail-Preflight $reason
    exit 2
} finally {
    $OutputEncoding=$oldOutput
    [Console]::OutputEncoding=$oldConsole
}
