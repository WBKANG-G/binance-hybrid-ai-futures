function Baseline-Money([object]$Value) {
    Inspect-Require ($Value -is [string] -and $Value -cmatch '^-?[0-9]{1,20}(\.[0-9]{1,18})?$' -and ($Value.Replace('-','').Replace('.','')).Length -le 28) 'BASELINE_MONEY_INVALID'
    $number=[decimal]::Parse($Value,([Globalization.NumberStyles]::AllowLeadingSign -bor [Globalization.NumberStyles]::AllowDecimalPoint),[Globalization.CultureInfo]::InvariantCulture)
    return $number
}
function Baseline-Time([object]$Value) {
    if ($Value -is [datetime]) {$Value=$Value.ToUniversalTime().ToString('o')}
    Inspect-Require ($Value -is [string] -and $Value -cmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,7})?(Z|\+00:00)$') 'BASELINE_TIME_INVALID'
    return $Value
}
function Baseline-Flag([object]$Value,[bool]$Expected) {Inspect-Require ($Value -is [bool] -and $Value -eq $Expected) 'BASELINE_RECORDED_FLAG_INVALID'}
function Baseline-Review([object]$Adoption,[object]$Complete,[object]$Commit) {
    Inspect-Require ($Adoption.policy -ceq 'OPERATOR_ATTESTED_ACCOUNTED_DAY13_ADOPTION_V1' -and $Adoption.old_row.account_id -ceq 'DEMO-WINDOWS-ISOLATED-BTC-V1') 'BASELINE_SOURCE_SCOPE_INVALID'
    $s=$Adoption.old_row.state;$v=$s.value
    Inspect-Require ($s.mode -ceq 'DEMO' -and @($s.orders.PSObject.Properties).Count -eq 34 -and @($s.requests.PSObject.Properties|Where-Object {@('UNKNOWN','SUBMITTING','CANCEL_REQUESTED','NOT_FOUND_UNRESOLVED') -ccontains $_.Value}).Count -eq 4) 'BASELINE_PRIOR_LEDGER_SCOPE_INVALID'
    Baseline-Flag $s.paused $true
    Inspect-Require ($s.daily_key -ceq '2026-11-12' -and $s.weekly_key -ceq '2026-11-09' -and $Complete.accounted_day13_adoption -ceq 'APPLIED_VERIFIED_KEEP_PAUSED' -and $Complete.day_kst -ceq '2026-11-13' -and $Complete.week_kst_preserved -ceq $s.weekly_key) 'BASELINE_PERIOD_LINK_INVALID'
    Inspect-Require ($Commit.policy -ceq $Adoption.policy -and $Commit.operation_id -ceq $Complete.operation_id -and $Commit.operation_id -cmatch '^[0-9a-f-]{36}$' -and $Commit.new_full_ledger_sha256 -cmatch '^[0-9a-f]{64}$' -and ($Adoption.old_row.fence -is [int] -or $Adoption.old_row.fence -is [long]) -and ($Commit.new_fence -is [int] -or $Commit.new_fence -is [long]) -and $Adoption.old_row.fence -ge 0 -and $Adoption.old_row.fence -lt [long]::MaxValue -and $Commit.new_fence -eq ($Adoption.old_row.fence+1)) 'BASELINE_COMMIT_LINK_INVALID'
    $components=[ordered]@{}
    foreach ($name in @('initial','realized','unrealized','fees','funding','net_deposits')) {$null=Baseline-Money $v.$name;$components[$name]=$v.$name}
    Inspect-Require ((Baseline-Money $v.initial) -gt 0 -and (Baseline-Money $v.fees) -ge 0) 'BASELINE_VALUE_INVALID'
    $money=[ordered]@{}
    foreach ($name in @('daily_base','weekly_base','high_water','available_balance')) {$number=Baseline-Money $s.$name;Inspect-Require ($number -ge 0 -and ($name -ceq 'available_balance' -or $number -gt 0)) 'BASELINE_VALUE_INVALID';$money[$name]=$s.$name}
    Inspect-Require ((Baseline-Money $Complete.daily_base_usdt) -eq (Baseline-Money $s.daily_base) -and (Baseline-Money $Complete.weekly_base_usdt_preserved) -eq (Baseline-Money $s.weekly_base) -and (Baseline-Money $Complete.high_water_usdt_preserved) -eq (Baseline-Money $s.high_water)) 'BASELINE_REPORTED_AMOUNT_LINK_INVALID'
    Inspect-Require ($Complete.baseline_source -ceq 'OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE') 'BASELINE_RECORDED_SOURCE_INVALID'
    foreach ($name in @('day_adopted','operator_attestation_used','prior_financial_history_preserved','only_reviewed_account_fields_changed','paused')) {Baseline-Flag $Complete.$name $true}
    foreach ($name in @('exact_day_midnight_valuation_verified','account_wide_trade_export_verified','exchange_account_identity_verified','execution_approved','orders_enabled','live_trading_enabled')) {Baseline-Flag $Complete.$name $false}
    # Decimal arithmetic only; this is a value derived from stored components, not an exchange balance.
    $equity=(Baseline-Money $v.initial)+(Baseline-Money $v.realized)+(Baseline-Money $v.unrealized)-(Baseline-Money $v.fees)+(Baseline-Money $v.funding)+(Baseline-Money $v.net_deposits)
    return [pscustomobject]@{
        prior_snapshot_day_kst=$s.daily_key;prior_snapshot_week_kst=$s.weekly_key
        prior_snapshot_value_as_of_utc=(Baseline-Time $v.as_of)
        recorded_value_components_usdt=[pscustomobject]$components
        derived_prior_snapshot_equity_usdt=$equity.ToString([Globalization.CultureInfo]::InvariantCulture)
        recorded_prior_daily_base_usdt=$money.daily_base;recorded_prior_weekly_base_usdt=$money.weekly_base
        recorded_prior_high_water_usdt=$money.high_water;recorded_prior_available_balance_usdt=$money.available_balance
        historical_report_day_kst=$Complete.day_kst;historical_report_week_kst=$Complete.week_kst_preserved
        historical_report_daily_base_usdt=$Complete.daily_base_usdt;historical_report_weekly_base_usdt=$Complete.weekly_base_usdt_preserved
        historical_report_high_water_usdt=$Complete.high_water_usdt_preserved
        historical_report_baseline_source=$Complete.baseline_source
        historical_report_operator_attestation_used=$true
        historical_report_exact_midnight_valuation_verified=$false
        historical_report_account_wide_trade_export_verified=$false
        historical_report_exchange_account_identity_verified=$false
        report_amounts_match_prior_snapshot=$true
        commit_record_link_fields_match=$true
        current_database_state_verified=$false;commit_hash_compared_to_current_database=$false
        historical_attestation_independently_verified_now=$false
        initial_funding_independently_verified=$false;historical_balance_provenance_complete=$false
        new_operator_attestation_received=$false;baseline_source_accepted_now=$false
        baseline_adoption_approved=$false;period_baselines_changed=$false
    }
}
function Invoke-BaselineReferenceInspection([string]$Root,[string]$ManifestPin) {
    $streams=New-Object Collections.ArrayList
    try {
        Inspect-Require ($ManifestPin -cmatch '^[0-9a-f]{64}$' -and ([IO.File]::GetAttributes($Root) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'BASELINE_ROOT_INVALID'
        $folders=@(Get-ChildItem -LiteralPath $Root -Directory -Filter 'cold-*');Inspect-Require ($folders.Count -le 32) 'BASELINE_SEARCH_LIMIT'
        $candidates=@()
        foreach ($folder in $folders) {
            Inspect-Require ($folder.Name -cmatch '^cold-20261114-[0-9a-f]{32}$' -and ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'BASELINE_DIRECTORY_INVALID'
            $path=Join-Path $folder.FullName 'manifest.json';if (-not [IO.File]::Exists($path)) {continue}
            $stream=Inspect-Open $path 16777216;$null=$streams.Add($stream)
            if ((Inspect-StreamHash $stream) -ceq $ManifestPin) {$candidates+=,[pscustomobject]@{folder=$folder.FullName;stream=$stream}}
        }
        Inspect-Require ($candidates.Count -eq 1) 'BASELINE_BACKUP_NOT_UNIQUE'
        $source=$candidates[0];$reader=New-Object IO.StreamReader($source.stream,(New-Object Text.UTF8Encoding($false,$true)),$true,1024,$true)
        try {$m=Inspect-Json $reader.ReadToEnd()} finally {$reader.Dispose()}
        $archive=Join-Path $source.folder 'runtime.tar';$runtime=Inspect-Open $archive 67108864;$null=$streams.Add($runtime)
        Inspect-Require ($runtime.Length -eq $m.groups.runtime.archive_bytes -and (Inspect-StreamHash $runtime) -ceq $m.groups.runtime.archive_sha256) 'BASELINE_ARCHIVE_HASH_MISMATCH'
        $pattern='^demo-runtime-20261111/accounted-day13-adoption-[0-9a-f-]{36}/intent\.json$'
        $entries=@($m.groups.runtime.entries|Where-Object {$_.kind -ceq 'file' -and $_.path -cmatch $pattern})
        Inspect-Require ($entries.Count -eq 1) 'BASELINE_RECORD_NOT_UNIQUE'
        $prefix=$entries[0].path.Substring(0,$entries[0].path.Length-'intent.json'.Length)
        $records=@{}
        foreach ($name in @('intent.json','commit-intent.json','complete.json')) {
            $members=@($m.groups.runtime.entries|Where-Object {$_.kind -ceq 'file' -and $_.path -ceq ($prefix+$name)})
            Inspect-Require ($members.Count -eq 1 -and $members[0].size -gt 0 -and $members[0].size -le 1048576) 'BASELINE_RECORD_NOT_UNIQUE'
            $records[$name]=Inspect-Json (Inspect-ReadArchive $archive $members[0].path)
        }
        $review=Baseline-Review $records['intent.json'] $records['complete.json'] $records['commit-intent.json']
        Inspect-Require ((Inspect-StreamHash $source.stream) -ceq $ManifestPin -and (Inspect-StreamHash $runtime) -ceq $m.groups.runtime.archive_sha256) 'BASELINE_SOURCE_CHANGED'
        return [pscustomobject]@{baseline_reference_inspection='PINNED_PRIOR_BASELINES_AND_HISTORICAL_REPORT_READ';source_snapshot='OLD_ROW_BEFORE_DAY13_ADOPTION_AND_STORED_REPORT_NOT_CURRENT_DATABASE';review=$review;files_written=$false;archive_extracted_to_disk=$false;raw_account_state_displayed=$false;raw_client_ids_displayed=$false;credentials_accessed=$false;docker_used=$false;services_started=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } catch {
        $reason='BASELINE_UNAVAILABLE';if ($_.Exception.Message -cmatch '^(BASELINE|INSPECT)_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
        return [pscustomobject]@{baseline_reference_inspection='BLOCKED_NO_WRITES';failure_category=$reason;files_written=$false;docker_used=$false;services_started=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } finally {foreach ($stream in $streams) {$stream.Dispose()}}
}
