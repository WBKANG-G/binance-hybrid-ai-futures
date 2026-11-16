function Inspect-Require([bool]$Condition,[string]$Code) {if (-not $Condition) {throw $Code}}
function Inspect-Hash([byte[]]$Bytes) {$h=[Security.Cryptography.SHA256]::Create();try {return ([BitConverter]::ToString($h.ComputeHash($Bytes))).Replace('-','').ToLowerInvariant()} finally {$h.Dispose()}}
function Inspect-Open([string]$Path,[long]$Limit) {
    Inspect-Require (([IO.File]::GetAttributes($Path) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'INSPECT_REPARSE_POINT'
    $s=New-Object IO.FileStream($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {Inspect-Require ($s.Length -gt 0 -and $s.Length -le $Limit) 'INSPECT_SIZE_LIMIT';return $s} catch {$s.Dispose();throw}
}
function Inspect-StreamHash([IO.Stream]$Stream) {$Stream.Position=0;$h=[Security.Cryptography.SHA256]::Create();try {$v=([BitConverter]::ToString($h.ComputeHash($Stream))).Replace('-','').ToLowerInvariant();$Stream.Position=0;return $v} finally {$h.Dispose()}}
function Inspect-Json([string]$Text) {try {$v=ConvertFrom-Json -InputObject $Text} catch {throw 'INSPECT_JSON_INVALID'};Inspect-Require ($v -is [pscustomobject]) 'INSPECT_JSON_INVALID';return $v}
function Inspect-Member([object]$Value,[string]$Name) {if ($null -eq $Value) {return $null};$p=$Value.PSObject.Properties[$Name];if ($null -eq $p) {return $null};return $p.Value}
function Inspect-ReadArchive([string]$Archive,[string]$Entry) {
    # The whole archive is hashed and held read-only before this call; never extract to disk.
    $text=(& tar.exe -xOf $Archive -- $Entry 2>$null | Out-String)
    Inspect-Require ($LASTEXITCODE -eq 0 -and $text.Length -le 1048576) 'INSPECT_ARCHIVE_READ_FAILED'
    return $text
}
function Inspect-Record([object]$Manifest,[string]$Archive,[string]$Pattern) {
    $entries=@($Manifest.groups.runtime.entries|Where-Object {$_.kind -ceq 'file' -and $_.path -cmatch $Pattern})
    Inspect-Require ($entries.Count -eq 1 -and $entries[0].size -gt 0 -and $entries[0].size -le 1048576) 'INSPECT_RECORD_NOT_UNIQUE'
    return (Inspect-Json (Inspect-ReadArchive $Archive $entries[0].path))
}
function Inspect-ServerId([object]$Value) {
    if ($null -eq $Value) {return $null}
    Inspect-Require (($Value -is [string] -or $Value -is [int] -or $Value -is [long]) -and [string]$Value -cmatch '^[1-9][0-9]{0,19}$') 'INSPECT_SERVER_ID_INVALID'
    return [string]$Value
}
function Inspect-Amount([object]$Value) {Inspect-Require ($Value -is [string] -and $Value -cmatch '^[0-9]{1,20}(\.[0-9]{1,18})?$') 'INSPECT_AMOUNT_INVALID';return $Value}
function Inspect-RequestRows([object]$State,[object]$Plan) {
    Inspect-Require ($State.mode -ceq 'DEMO' -and @($State.orders.PSObject.Properties).Count -eq 34 -and $Plan.policy -ceq 'HISTORICAL_ORIGINAL_ID_EVIDENCE_20261113_V1' -and @($Plan.plan).Count -eq 4) 'INSPECT_STATE_SCOPE_INVALID'
    $requests=@($State.requests.PSObject.Properties|Where-Object {@('UNKNOWN','SUBMITTING','CANCEL_REQUESTED','NOT_FOUND_UNRESOLVED') -ccontains $_.Value})
    Inspect-Require ($requests.Count -eq 4) 'INSPECT_REQUEST_COUNT_INVALID'
    $rows=@();$seen=@{}
    foreach ($request in $requests) {
        $id=$request.Name;Inspect-Require ($id.Length -gt 0 -and $id.Length -le 128) 'INSPECT_REQUEST_ID_INVALID'
        $pin=Inspect-Hash ([Text.Encoding]::UTF8.GetBytes($id))
        $p=@($Plan.plan|Where-Object {$_.request_reference_sha256 -ceq $pin})
        Inspect-Require ($p.Count -eq 1 -and $p[0].state -ceq $request.Value -and @('ENTRY','STOP_LOSS','EXIT') -ccontains $p[0].role -and -not $seen.ContainsKey($pin)) 'INSPECT_PLAN_LINK_INVALID'
        $seen[$pin]=$true;$role=$p[0].role
        $field=@{ENTRY='client_id';STOP_LOSS='algo_id';EXIT='exit_id'}[$role]
        $orders=@($State.orders.PSObject.Properties|Where-Object {(Inspect-Member $_.Value $field) -ceq $id})
        Inspect-Require ($orders.Count -eq 1) 'INSPECT_ORDER_LINK_INVALID'
        $o=$orders[0].Value;$s=$o.signal
        Inspect-Require ($s.environment -ceq 'DEMO' -and $s.symbol -ceq 'BTCUSDT' -and @('LONG','SHORT') -ccontains $s.direction -and $o.position_state -ceq 'CLOSED') 'INSPECT_ORDER_SCOPE_INVALID'
        $stamp=Inspect-Member $s 'signal_closed_candle_at'
        if ($stamp -is [datetime]) {$stamp=$stamp.ToUniversalTime().ToString('o')}
        Inspect-Require ($stamp -is [string] -and $stamp -cmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,7})?(Z|\+00:00)$') 'INSPECT_TIME_INVALID'
        $server=$null
        if ($role -ceq 'ENTRY') {$server=Inspect-ServerId (Inspect-Member $o 'server_order_id')}
        if ($role -ceq 'STOP_LOSS') {$server=Inspect-ServerId (Inspect-Member $State.algo_server_ids $id)}
        # The local model has no exit server-order-ID field. Never substitute the entry ID as it.
        $rows+=,[pscustomobject]@{role=$role;request_state=$request.Value;request_reference_sha256=$pin;symbol='BTCUSDT';direction=$s.direction;signal_closed_candle_at_utc=$stamp;quantity=(Inspect-Amount $o.quantity);entry_limit_price=(Inspect-Amount $s.suggested_limit_price);stop_trigger_price=(Inspect-Amount $s.initial_stop_price);server_id=$server;entry_server_order_id=(Inspect-ServerId (Inspect-Member $o 'server_order_id'));remote_resolution_verified=$false}
    }
    Inspect-Require (@($rows|Where-Object {$_.role -ceq 'ENTRY'}).Count -eq 2 -and @($rows|Where-Object {$_.role -ceq 'STOP_LOSS'}).Count -eq 1 -and @($rows|Where-Object {$_.role -ceq 'EXIT'}).Count -eq 1) 'INSPECT_ROLE_COUNTS_INVALID'
    return @($rows|Sort-Object role,request_reference_sha256)
}
function Invoke-RequestReferenceInspection([string]$Root,[string]$ManifestPin) {
    $streams=New-Object Collections.ArrayList
    try {
        Inspect-Require ($ManifestPin -cmatch '^[0-9a-f]{64}$' -and ([IO.File]::GetAttributes($Root) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'INSPECT_ROOT_INVALID'
        $folders=@(Get-ChildItem -LiteralPath $Root -Directory -Filter 'cold-*');Inspect-Require ($folders.Count -le 32) 'INSPECT_SEARCH_LIMIT'
        $backups=@()
        foreach ($folder in $folders) {
            Inspect-Require ($folder.Name -cmatch '^cold-20261114-[0-9a-f]{32}$' -and ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'INSPECT_DIRECTORY_INVALID'
            $path=Join-Path $folder.FullName 'manifest.json'
            if (-not [IO.File]::Exists($path)) {continue}
            $stream=Inspect-Open $path 16777216;$null=$streams.Add($stream)
            if ((Inspect-StreamHash $stream) -ceq $ManifestPin) {$backups+=,[pscustomobject]@{folder=$folder.FullName;stream=$stream}}
        }
        Inspect-Require ($backups.Count -eq 1) 'INSPECT_BACKUP_NOT_UNIQUE'
        $source=$backups[0];$reader=New-Object IO.StreamReader($source.stream,(New-Object Text.UTF8Encoding($false,$true)),$true,1024,$true)
        try {$m=Inspect-Json $reader.ReadToEnd()} finally {$reader.Dispose()}
        $archive=Join-Path $source.folder 'runtime.tar';$runtime=Inspect-Open $archive 67108864;$null=$streams.Add($runtime)
        Inspect-Require ($runtime.Length -eq $m.groups.runtime.archive_bytes -and (Inspect-StreamHash $runtime) -ceq $m.groups.runtime.archive_sha256) 'INSPECT_ARCHIVE_HASH_MISMATCH'
        $adopt=Inspect-Record $m $archive '^demo-runtime-20261111/accounted-day13-adoption-[0-9a-f-]{36}/intent\.json$'
        $plan=Inspect-Record $m $archive '^demo-runtime-20261111/historical-request-evidence-[0-9a-f-]{36}/intent\.json$'
        Inspect-Require ($adopt.policy -ceq 'OPERATOR_ATTESTED_ACCOUNTED_DAY13_ADOPTION_V1' -and $adopt.old_row.account_id -ceq 'DEMO-WINDOWS-ISOLATED-BTC-V1' -and $plan.account -ceq $adopt.old_row.account_id) 'INSPECT_ACCOUNT_LINK_INVALID'
        $rows=@(Inspect-RequestRows $adopt.old_row.state $plan)
        Inspect-Require ((Inspect-StreamHash $source.stream) -ceq $ManifestPin -and (Inspect-StreamHash $runtime) -ceq $m.groups.runtime.archive_sha256) 'INSPECT_SOURCE_CHANGED'
        return [pscustomobject]@{request_reference_inspection='PINNED_PRIOR_LEDGER_FOUR_REQUEST_REFERENCES_READ';source_snapshot='OLD_ROW_BEFORE_DAY13_ADOPTION_NOT_CURRENT_DATABASE';request_count=4;requests=$rows;files_written=$false;archive_extracted_to_disk=$false;raw_client_ids_displayed=$false;credentials_accessed=$false;docker_used=$false;services_started=$false;actual_http_attempts=0;order_writes=0;current_database_state_verified=$false;remote_resolution_verified=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } catch {
        $reason='INSPECT_UNAVAILABLE';if ($_.Exception.Message -cmatch '^INSPECT_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
        return [pscustomobject]@{request_reference_inspection='BLOCKED_NO_WRITES';failure_category=$reason;files_written=$false;docker_used=$false;services_started=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } finally {foreach ($stream in $streams) {$stream.Dispose()}}
}
