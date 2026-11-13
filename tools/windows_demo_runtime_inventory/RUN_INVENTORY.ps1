param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=$PSScriptRoot
$stage='TRANSFER_VERIFICATION'
$oldOutput=$OutputEncoding
$oldConsole=[Console]::OutputEncoding
$OutputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
function Fail-Inventory([string]$Code) {
    [pscustomobject]@{runtime_inventory='BLOCKED_KEEP_PAUSED';stage=$stage;failure_category=$Code;automatic_retry=$false;database_changed=$false;source_files_changed=$false;api_replaced=$false;exchange_credentials_accessed=$false;credential_session_opened=$false;shared_journal_opened=$false;clock_recovery_attempted=$false;reference_generated=$false;offline_copy_performed=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
}
function Read-InventoryDockerField([string]$Identity,[string]$Format) {
    $lines=@(& docker inspect --format $Format $Identity 2>$null)
    $code=$LASTEXITCODE
    if ($code -ne 0 -or $lines.Count -ne 1) { throw 'INVENTORY_CONTAINER_UNAVAILABLE' }
    return [string]$lines[0]
}
function Assert-InventoryOutput([object]$Result,[object]$Configuration) {
    if ($Result -isnot [pscustomobject]) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    $fields=@('runtime_inventory','stage','failure_category','sqlstate','source_reference','baseline_reference','baseline_files_total','baseline_files_verified','candidate_files_total','candidate_files_matching','changed_or_missing_baseline_files','changed_or_missing_candidate_files','table_presence','account_found','paused','risk_state','stored_day_kst','stored_week_kst','current_day_kst','expected_day_kst','period_keys_match','local_orders','request_state_counts','database_configuration_used','candidate_table_names_present','metadata_only','database_changed','source_files_changed','api_replaced','exchange_credentials_accessed','credential_session_opened','shared_journal_opened','clock_recovery_attempted','reference_generated','offline_copy_performed','automatic_retry','actual_http_attempts','order_writes','execution_approved','orders_enabled','live_trading_enabled')
    $actual=@($Result.PSObject.Properties.Name)
    if ((@($actual | Sort-Object) -join '|') -ne (@($fields | Sort-Object) -join '|')) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    if (@('READ_ONLY_INVENTORY_COMPLETE_KEEP_PAUSED','BLOCKED_KEEP_PAUSED') -notcontains $Result.runtime_inventory) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    if (@('CONFIGURATION','SOURCE_FILES','DATABASE_CONFIGURATION','DATABASE_SCHEMA','ACCOUNT_PROJECTION','COMPLETE') -notcontains $Result.stage) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    if (@('INPUT_OR_STATE_INVALID','SOURCE_OR_DATABASE_READ_FAILED','DATABASE_NOT_CONFIGURED','FINANCIAL_TABLES_MISSING','ACCOUNT_NOT_FOUND','NONE') -notcontains $Result.failure_category) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    if ($null -ne $Result.sqlstate -and ($Result.sqlstate -isnot [string] -or $Result.sqlstate -notmatch '^[A-Z0-9]{5}$')) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    if ($Result.source_reference -ne $Configuration.source_reference -or $Result.baseline_reference -ne $Configuration.baseline_reference) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    foreach ($name in @('database_changed','source_files_changed','api_replaced','exchange_credentials_accessed','credential_session_opened','shared_journal_opened','clock_recovery_attempted','reference_generated','offline_copy_performed','automatic_retry','execution_approved','orders_enabled','live_trading_enabled')) {
        if ($Result.$name -isnot [bool] -or $Result.$name -ne $false) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    }
    if ($Result.metadata_only -isnot [bool] -or $Result.metadata_only -ne $true) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    foreach ($name in @('account_found','database_configuration_used','candidate_table_names_present')) {
        if ($Result.$name -isnot [bool]) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    }
    foreach ($name in @('paused','period_keys_match')) {
        if ($null -ne $Result.$name -and $Result.$name -isnot [bool]) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    }
    foreach ($name in @('baseline_files_total','baseline_files_verified','candidate_files_total','candidate_files_matching','local_orders','actual_http_attempts','order_writes')) {
        if (($Result.$name -isnot [int] -and $Result.$name -isnot [long]) -or $Result.$name -lt 0 -or $Result.$name -gt 1000000) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    }
    if ($Result.actual_http_attempts -ne 0 -or $Result.order_writes -ne 0) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    foreach ($name in @('stored_day_kst','stored_week_kst','current_day_kst','expected_day_kst')) {
        if ($null -ne $Result.$name -and ($Result.$name -isnot [string] -or $Result.$name -notmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    }
    if ($null -ne $Result.risk_state -and @('NORMAL','COOLDOWN','DAILY_LOCK','WEEKLY_LOCK','DRAWDOWN_LOCK','UNHEALTHY','EMERGENCY') -notcontains $Result.risk_state) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    foreach ($group in @('baseline','candidate')) {
        $name='changed_or_missing_'+$group+'_files'
        $known=@($Configuration.($group+'_files').PSObject.Properties.Name)
        if ($Result.$name -isnot [System.Array] -or $Result.$name.Count -gt 128) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
        foreach ($entry in $Result.$name) { if ($entry -isnot [string] -or $known -notcontains $entry) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' } }
    }
    if ($Result.baseline_files_total -ne @($Configuration.baseline_files.PSObject.Properties).Count -or $Result.candidate_files_total -ne @($Configuration.candidate_files.PSObject.Properties).Count) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    if ($Result.baseline_files_verified + $Result.changed_or_missing_baseline_files.Count -ne $Result.baseline_files_total -or $Result.candidate_files_matching + $Result.changed_or_missing_candidate_files.Count -ne $Result.candidate_files_total) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    if ($Result.expected_day_kst -ne $Configuration.expected_day_kst) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    if ($Result.runtime_inventory -eq 'READ_ONLY_INVENTORY_COMPLETE_KEEP_PAUSED') {
        if ($Result.stage -ne 'COMPLETE' -or $Result.failure_category -ne 'NONE' -or $null -ne $Result.sqlstate -or $Result.account_found -ne $true -or $Result.database_configuration_used -ne $true -or $Result.local_orders -gt 128 -or $Result.paused -isnot [bool] -or $Result.period_keys_match -isnot [bool] -or $null -eq $Result.risk_state) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    }
    $tables=@('demo_accounts','demo_events','demo_outbox','demo_read_audit_bindings','demo_read_audit_events','read_response_evidence_bindings_v2','read_response_evidence_events_v2','wire_checkpoint_installation_v2','wire_checkpoint_bindings_v2','wire_checkpoint_events_v2')
    if ($Result.table_presence -isnot [pscustomobject] -or (@($Result.table_presence.PSObject.Properties.Name | Sort-Object) -join '|') -ne (@($tables | Sort-Object) -join '|')) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    foreach ($name in $tables) { if ($Result.table_presence.$name -isnot [bool]) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' } }
    $states=@('UNKNOWN','SUBMITTING','CANCEL_REQUESTED','ACKNOWLEDGED','NOT_SENT','REJECTED','NOT_FOUND_UNRESOLVED','OTHER')
    if ($Result.request_state_counts -isnot [pscustomobject] -or (@($Result.request_state_counts.PSObject.Properties.Name | Sort-Object) -join '|') -ne (@($states | Sort-Object) -join '|')) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    foreach ($name in $states) {
        if (($Result.request_state_counts.$name -isnot [int] -and $Result.request_state_counts.$name -isnot [long]) -or $Result.request_state_counts.$name -lt 0 -or $Result.request_state_counts.$name -gt 768) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    }
}
try {
    if ($ManifestSHA256 -notmatch '^[0-9a-f]{64}$') { throw 'INVENTORY_TRANSFER_INVALID' }
    $manifestPath=Join-Path $root 'TRANSFER_MANIFEST.json'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant() -ne $ManifestSHA256) { throw 'INVENTORY_TRANSFER_INVALID' }
    $manifest=Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $path=[IO.Path]::GetFullPath((Join-Path $root $entry.Name))
        if (-not $path.StartsWith(($root+[IO.Path]::DirectorySeparatorChar),[StringComparison]::OrdinalIgnoreCase)) { throw 'INVENTORY_TRANSFER_INVALID' }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -ne $entry.Value) { throw 'INVENTORY_TRANSFER_INVALID' }
    }
    Write-Output 'TRANSFER_FILES_VERIFIED'
    $configuration=Get-Content -Raw -LiteralPath (Join-Path $root 'INVENTORY_CONFIG.json') | ConvertFrom-Json
    $stage='CONTAINER_IDENTITY'
    $id=Read-InventoryDockerField 'hybrid-windows-api-1' '{{.Id}}'
    if ($id -notmatch '^[0-9a-f]{64}$') { throw 'INVENTORY_CONTAINER_UNAVAILABLE' }
    if ((Read-InventoryDockerField $id '{{.Name}}') -ne '/hybrid-windows-api-1') { throw 'INVENTORY_CONTAINER_CONTEXT_MISMATCH' }
    if ((Read-InventoryDockerField $id '{{.State.Running}}') -ne 'true') { throw 'INVENTORY_CONTAINER_UNAVAILABLE' }
    if ((Read-InventoryDockerField $id '{{.Config.WorkingDir}}') -ne '/app') { throw 'INVENTORY_CONTAINER_CONTEXT_MISMATCH' }
    if (@('10001','10001:10001') -notcontains (Read-InventoryDockerField $id '{{.Config.User}}')) { throw 'INVENTORY_CONTAINER_CONTEXT_MISMATCH' }
    $labelsText=Read-InventoryDockerField $id '{{json .Config.Labels}}'
    if ($labelsText.Length -gt 16384) { throw 'INVENTORY_CONTAINER_CONTEXT_MISMATCH' }
    $labels=ConvertFrom-Json -InputObject $labelsText
    if ($labels -isnot [pscustomobject] -or $labels.'com.docker.compose.project' -ne 'hybrid-windows' -or $labels.'com.docker.compose.service' -ne 'api') { throw 'INVENTORY_CONTAINER_CONTEXT_MISMATCH' }
    $stage='READ_ONLY_WORKER'
    $worker=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'worker.py')))
    $config=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $root 'INVENTORY_CONFIG.json')))
    $payload="import base64,json`nCONFIG=json.loads(base64.b64decode('$config'))`nexec(compile(base64.b64decode('$worker'),'<runtime-inventory>','exec'))"
    $output=@($payload | & docker exec -i $id python -I -B - 2>$null)
    $code=$LASTEXITCODE
    $joined=$output -join "`n"
    if ($joined.Length -gt 32768) { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    $result=ConvertFrom-Json -InputObject $joined
    Assert-InventoryOutput $result $configuration
    if ($code -ne 0 -and $result.runtime_inventory -ne 'BLOCKED_KEEP_PAUSED') { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    if ($code -eq 0 -and $result.runtime_inventory -ne 'READ_ONLY_INVENTORY_COMPLETE_KEEP_PAUSED') { throw 'INVENTORY_WORKER_OUTPUT_INVALID' }
    $result | ConvertTo-Json -Depth 8 -Compress | Write-Output
    if ($code -ne 0) { exit 2 }
    Write-Output 'RUNTIME_INVENTORY_COMPLETED_KEEP_PAUSED_NO_ORDERS'
    exit 0
} catch {
    $reason='INVENTORY_RUNNER_FAILED'
    $allowed=@('INVENTORY_TRANSFER_INVALID','INVENTORY_CONTAINER_UNAVAILABLE','INVENTORY_CONTAINER_CONTEXT_MISMATCH','INVENTORY_WORKER_OUTPUT_INVALID')
    if ($allowed -contains $_.Exception.Message) { $reason=$_.Exception.Message }
    Fail-Inventory $reason
    exit 2
} finally {
    $OutputEncoding=$oldOutput
    [Console]::OutputEncoding=$oldConsole
}
