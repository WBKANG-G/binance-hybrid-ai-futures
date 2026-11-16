function NoSend-Review([object]$State,[object[]]$Rows,[object]$Original,[object]$ExitReport) {
    Inspect-Require ($Rows.Count -eq 4 -and $Original.policy -ceq 'HISTORICAL_ORIGINAL_ID_EVIDENCE_20261113_V1' -and $ExitReport.policy -ceq 'PREVIOUSLY_UNQUERIED_EXIT_EVIDENCE_20261113_V1') 'NO_SEND_REPORT_SCOPE_INVALID'
    $records=@($Original.observations)+@($ExitReport.observations)
    Inspect-Require ($records.Count -eq 4 -and $Original.requests_observed -eq @($Original.observations).Count -and $ExitReport.requests_observed -eq @($ExitReport.observations).Count) 'NO_SEND_OBSERVATION_COUNT_INVALID'
    $mapProperty=$State.PSObject.Properties['request_no_send_evidence'];$mapPresent=($null -ne $mapProperty)
    $map=$null
    if ($mapPresent) {$map=$mapProperty.Value;Inspect-Require ($map -is [pscustomobject] -and @($map.PSObject.Properties).Count -le 4096) 'NO_SEND_MAP_INVALID'}
    $result=@();$reasonCount=0;$seen=@{}
    foreach ($row in $Rows) {
        $pin=$row.request_reference_sha256
        Inspect-Require ($pin -cmatch '^[0-9a-f]{64}$' -and -not $seen.ContainsKey($pin)) 'NO_SEND_REQUEST_LINK_INVALID';$seen[$pin]=$true
        $requests=@($State.requests.PSObject.Properties|Where-Object {(Inspect-Hash ([Text.Encoding]::UTF8.GetBytes($_.Name))) -ceq $pin})
        Inspect-Require ($requests.Count -eq 1 -and $requests[0].Value -ceq $row.request_state) 'NO_SEND_REQUEST_LINK_INVALID'
        $record=@($records|Where-Object {$_.request_reference_sha256 -ceq $pin -and $_.role -ceq $row.role})
        Inspect-Require ($record.Count -eq 1 -and @('FILLED','CANCELED','EXPIRED','NEW','PARTIALLY_FILLED','REJECTED','EXPIRED_IN_MATCH','TRIGGERING','TRIGGERED','FINISHED','NOT_FOUND_UNRESOLVED') -ccontains $record[0].observed_status -and $record[0].response_identity_fields_match -is [bool] -and $record[0].remote_resolution_verified -is [bool] -and -not $record[0].remote_resolution_verified) 'NO_SEND_OBSERVATION_LINK_INVALID'
        if ($record[0].observed_status -ceq 'NOT_FOUND_UNRESOLVED') {Inspect-Require (-not $record[0].response_identity_fields_match) 'NO_SEND_OBSERVATION_LINK_INVALID'} else {Inspect-Require $record[0].response_identity_fields_match 'NO_SEND_OBSERVATION_LINK_INVALID'}
        $reason=$null;$entryPresent=$false
        if ($mapPresent) {
            $entry=$map.PSObject.Properties[$requests[0].Name]
            if ($null -ne $entry) {
                $entryPresent=$true;$reason=$entry.Value
                Inspect-Require ($reason -is [string] -and @('LOCAL_VALIDATION_FAILED','POSITION_ALREADY_FLAT','POSITION_DIRECTION_MISMATCH') -ccontains $reason) 'NO_SEND_REASON_INVALID'
                $reasonCount++
            }
        }
        $finding='NO_DURABLE_NO_SEND_REASON_IN_THIS_PRIOR_SNAPSHOT'
        if (-not $mapPresent) {$finding='NO_SEND_MAP_ABSENT_IN_THIS_PRIOR_SNAPSHOT'}
        if ($entryPresent) {$finding='RECORDED_REASON_PRESENT_BUT_UNRESOLVED_REQUEST_STATE_REQUIRES_SEPARATE_REVIEW'}
        $result+=,[pscustomobject]@{
            role=$row.role;request_reference_sha256=$pin;recorded_request_state=$row.request_state
            prior_snapshot_no_send_map_present=$mapPresent;recorded_no_send_reason_present=$entryPresent;recorded_no_send_reason=$reason
            recorded_reason_with_unresolved_state_requires_review=$entryPresent
            stored_query_observed_status=$record[0].observed_status
            stored_query_response_identity_fields_match=$record[0].response_identity_fields_match
            finding=$finding;actual_submission_or_non_submission_independently_verified=$false
            not_found_treated_as_not_sent=$false;remote_resolution_verified=$false
            request_resolution_authorized=$false;automatically_resolvable=$false
        }
    }
    return [pscustomobject]@{scope='PINNED_PRIOR_SNAPSHOT_AND_STORED_READ_REPORTS_ONLY';no_send_map_present=$mapPresent;recorded_reason_count_for_four_requests=$reasonCount;requests=$result;current_database_state_verified=$false;event_transition_history_replayed=$false;wire_send_or_non_send_independently_verified=$false;stored_no_send_reason_independently_verified=$false;not_found_proves_no_submission=$false;current_flat_position_proves_no_submission=$false;request_states_changed=$false;request_resolution_authorized=$false;resend_authorized=$false;remote_resolution_verified=$false}
}
function Invoke-NoSendReferenceInspection([string]$Root,[string]$ManifestPin) {
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
        $original=Inspect-Record $m $archive '^demo-runtime-20261111/historical-request-evidence-[0-9a-f-]{36}/complete\.json$'
        $exitReport=Inspect-Record $m $archive '^demo-runtime-20261111/historical-exit-evidence-[0-9a-f-]{36}/complete\.json$'
        Inspect-Require ($original.operation_id -ceq $plan.operation_id -and $original.operation_id -cmatch '^[0-9a-f-]{36}$') 'NO_SEND_REPORT_OPERATION_LINK_INVALID'
        $review=NoSend-Review $adopt.old_row.state $rows $original $exitReport
        Inspect-Require ((Inspect-StreamHash $source.stream) -ceq $ManifestPin -and (Inspect-StreamHash $runtime) -ceq $m.groups.runtime.archive_sha256) 'INSPECT_SOURCE_CHANGED'
        return [pscustomobject]@{no_send_reference_inspection='PINNED_PRIOR_NO_SEND_RECORDS_AND_READ_OBSERVATIONS_REVIEWED';source_snapshot='OLD_ROW_BEFORE_DAY13_ADOPTION_AND_STORED_READ_REPORTS_NOT_CURRENT_DATABASE';request_count=4;review=$review;files_written=$false;archive_extracted_to_disk=$false;raw_client_ids_displayed=$false;credentials_accessed=$false;docker_used=$false;services_started=$false;actual_http_attempts=0;order_writes=0;current_database_state_verified=$false;remote_resolution_verified=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } catch {
        $reason='NO_SEND_UNAVAILABLE';if ($_.Exception.Message -cmatch '^(NO_SEND|INSPECT)_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
        return [pscustomobject]@{no_send_reference_inspection='BLOCKED_NO_WRITES';failure_category=$reason;files_written=$false;docker_used=$false;services_started=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } finally {foreach ($stream in $streams) {$stream.Dispose()}}
}
