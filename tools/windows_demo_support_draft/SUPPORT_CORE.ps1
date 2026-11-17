function Support-Targets([object]$State,[object[]]$Rows,[object]$Original,[object]$ExitReport) {
    Inspect-Require ($Original.policy -ceq 'HISTORICAL_ORIGINAL_ID_EVIDENCE_20261113_V1' -and $ExitReport.policy -ceq 'PREVIOUSLY_UNQUERIED_EXIT_EVIDENCE_20261113_V1') 'SUPPORT_REPORT_SCOPE_INVALID'
    $targets=@{}
    foreach ($role in @('STOP_LOSS','EXIT')) {
        $row=@($Rows|Where-Object {$_.role -ceq $role});Inspect-Require ($row.Count -eq 1) 'SUPPORT_TARGET_NOT_UNIQUE'
        $request=@($State.requests.PSObject.Properties|Where-Object {(Inspect-Hash ([Text.Encoding]::UTF8.GetBytes($_.Name))) -ceq $row[0].request_reference_sha256})
        Inspect-Require ($request.Count -eq 1 -and $request[0].Value -ceq $row[0].request_state) 'SUPPORT_REQUEST_LINK_INVALID'
        $id=$request[0].Name
        if ($role -ceq 'STOP_LOSS') {Inspect-Require ($id -cmatch '^DS-[0-9a-f]{30}$') 'SUPPORT_IDENTIFIER_SHAPE_INVALID';$report=$Original}
        else {Inspect-Require ($id -cmatch '^DX-[0-9a-f]{30}(-[2-9][0-9]{0,4})?$') 'SUPPORT_IDENTIFIER_SHAPE_INVALID';$report=$ExitReport}
        $observed=@($report.observations|Where-Object {$_.role -ceq $role -and $_.request_reference_sha256 -ceq $row[0].request_reference_sha256})
        Inspect-Require ($observed.Count -eq 1 -and $observed[0].observed_status -ceq 'NOT_FOUND_UNRESOLVED' -and $observed[0].response_identity_fields_match -is [bool] -and -not $observed[0].response_identity_fields_match -and $observed[0].remote_resolution_verified -is [bool] -and -not $observed[0].remote_resolution_verified) 'SUPPORT_OBSERVATION_LINK_INVALID'
        $targets[$role]=[pscustomobject]@{id=$id;row=$row[0]}
    }
    $stop=$targets['STOP_LOSS'];$exit=$targets['EXIT']
    Inspect-Require ($stop.row.entry_server_order_id -ceq $exit.row.entry_server_order_id -and $stop.row.quantity -ceq $exit.row.quantity -and $stop.row.entry_limit_price -ceq $exit.row.entry_limit_price -and $stop.row.signal_closed_candle_at_utc -ceq $exit.row.signal_closed_candle_at_utc -and $stop.row.direction -ceq 'LONG' -and $exit.row.direction -ceq 'LONG') 'SUPPORT_RELATED_ORDER_MISMATCH'
    $server=Inspect-ServerId $stop.row.entry_server_order_id;Inspect-Require ($null -ne $server) 'SUPPORT_RELATED_ORDER_MISSING'
    return [pscustomobject]@{stop_id=$stop.id;exit_id=$exit.id;stop_reference=$stop.row.request_reference_sha256;exit_reference=$exit.row.request_reference_sha256;entry_server_id=$server;quantity=$stop.row.quantity;entry_price=$stop.row.entry_limit_price}
}
function Support-Text([string]$Template,[object]$Target) {
    $values=@{STOP_CLIENT_ID=$Target.stop_id;EXIT_CLIENT_ID=$Target.exit_id;ENTRY_SERVER_ID=$Target.entry_server_id;QUANTITY=$Target.quantity;ENTRY_PRICE=$Target.entry_price}
    foreach ($key in $values.Keys) {Inspect-Require ($Template.Contains('{{'+$key+'}}')) 'SUPPORT_TEMPLATE_INVALID';$Template=$Template.Replace('{{'+$key+'}}',[string]$values[$key])}
    Inspect-Require (-not $Template.Contains('{{') -and [Text.Encoding]::UTF8.GetByteCount($Template) -le 32768) 'SUPPORT_TEMPLATE_INVALID'
    return $Template
}
function Support-WriteNew([string]$Path,[byte[]]$Bytes) {
    Inspect-Require ($Bytes.Length -gt 0 -and $Bytes.Length -le 32768) 'SUPPORT_FILE_SIZE_LIMIT'
    $stream=New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {$stream.Write($Bytes,0,$Bytes.Length);$stream.Flush($true)} finally {$stream.Dispose()}
}
function Invoke-SupportDraft([string]$Root,[string]$ManifestPin,[string]$Template,[switch]$CheckOnly,[switch]$CreateDraft) {
    $streams=New-Object Collections.ArrayList;$directory=$null;$script:ReferenceAttemptPath=$null
    try {
        Inspect-Require (($CheckOnly -and -not $CreateDraft) -or ($CreateDraft -and -not $CheckOnly)) 'SUPPORT_EXPLICIT_MODE_REQUIRED'
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
        $original=Inspect-Record $m $archive '^demo-runtime-20261111/historical-request-evidence-[0-9a-f-]{36}/complete\.json$'
        $exitReport=Inspect-Record $m $archive '^demo-runtime-20261111/historical-exit-evidence-[0-9a-f-]{36}/complete\.json$'
        $target=Support-Targets $adopt.old_row.state $rows $original $exitReport
        $body=Support-Text $Template $target
        Inspect-Require ((Inspect-StreamHash $source.stream) -ceq $ManifestPin -and (Inspect-StreamHash $runtime) -ceq $m.groups.runtime.archive_sha256) 'INSPECT_SOURCE_CHANGED'
        if ($CheckOnly) {return [pscustomobject]@{support_draft='PINNED_TWO_REQUEST_DRAFT_INPUTS_CHECKED_NO_FILE_CREATED';request_count=2;draft_created=$false;raw_client_ids_displayed=$false;source_files_written=$false;credentials_accessed=$false;docker_used=$false;services_started=$false;actual_http_attempts=0;messages_sent=0;automatic_retry=$false;request_resolution_authorized=$false;resend_authorized=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}}
        $directory=Reference-NewDirectory ([Guid]::NewGuid().ToString('N'))
        $draft=Join-Path $directory 'REQUEST_CLARIFICATION_KO.txt'
        Support-WriteNew $draft ([Text.Encoding]::UTF8.GetBytes($body))
        $draftBytes=[IO.File]::ReadAllBytes($draft)
        Inspect-Require ((Inspect-Hash $draftBytes) -ceq (Inspect-Hash ([Text.Encoding]::UTF8.GetBytes($body)))) 'SUPPORT_READBACK_MISMATCH'
        $record=[ordered]@{policy='LOCAL_TWO_REQUEST_SUPPORT_DRAFT_V1';created_at_utc=[DateTime]::UtcNow.ToString('o');original_manifest_sha256=$ManifestPin;runtime_archive_sha256=$m.groups.runtime.archive_sha256;draft_sha256=(Inspect-Hash $draftBytes);request_reference_sha256=@($target.stop_reference,$target.exit_reference);contains_client_order_identifiers=$true;contains_api_credentials=$false;raw_client_ids_displayed=$false;messages_sent=0;source_files_written=$false;credentials_accessed=$false;actual_http_attempts=0;request_resolution_authorized=$false;resend_authorized=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
        $recordBytes=[Text.Encoding]::UTF8.GetBytes(($record|ConvertTo-Json -Depth 8 -Compress)+"`n")
        $receipt=Join-Path $directory 'DRAFT_RECEIPT.json'
        Support-WriteNew $receipt $recordBytes
        Inspect-Require ((Inspect-Hash ([IO.File]::ReadAllBytes($receipt))) -ceq (Inspect-Hash $recordBytes)) 'SUPPORT_READBACK_MISMATCH'
        Inspect-Require ((Inspect-StreamHash $source.stream) -ceq $ManifestPin -and (Inspect-StreamHash $runtime) -ceq $m.groups.runtime.archive_sha256) 'SUPPORT_SOURCE_CHANGED'
        return [pscustomobject]@{support_draft='PRIVATE_TWO_REQUEST_SUPPORT_DRAFT_CREATED_NOT_SENT';private_draft_file=$draft;draft_sha256=(Inspect-Hash $draftBytes);request_count=2;draft_created=$true;contains_client_order_identifiers=$true;raw_client_ids_displayed=$false;source_files_written=$false;credentials_accessed=$false;docker_used=$false;services_started=$false;actual_http_attempts=0;messages_sent=0;automatic_retry=$false;request_resolution_authorized=$false;resend_authorized=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } catch {
        $reason='SUPPORT_DRAFT_UNAVAILABLE';if ($null -eq $directory -and $null -ne $script:ReferenceAttemptPath) {$directory=$script:ReferenceAttemptPath};if ($_.Exception.Message -cmatch '^(SUPPORT|INSPECT|REFERENCE)_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
        return [pscustomobject]@{support_draft='BLOCKED_NO_AUTOMATIC_RETRY';failure_category=$reason;retained_partial_draft_directory=$directory;draft_creation_may_have_started=($null -ne $directory);source_files_written=$false;raw_client_ids_displayed=$false;messages_sent=0;automatic_retry=$false;docker_used=$false;services_started=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } finally {foreach ($stream in $streams) {$stream.Dispose()}}
}
