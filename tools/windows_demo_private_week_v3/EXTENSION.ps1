$script:PrivateLateResultDiagnostic=$null
$script:PriorPrivateV2StoppedVerified=$false
function Assert-PrivateLateLeaseResult([object]$Pipeline) {
    $flag=$null;$seconds=$null
    if ($null -ne $Pipeline.PSObject.Properties['private_lease_opened_at_capture']) {$flag=$Pipeline.private_lease_opened_at_capture}
    if ($null -ne $Pipeline.PSObject.Properties['private_preparation_seconds']) {$seconds=$Pipeline.private_preparation_seconds}
    $typeName='NULL';if ($null -ne $seconds) {$typeName=$seconds.GetType().FullName}
    $numeric=($seconds -is [decimal] -or $seconds -is [double] -or $seconds -is [int] -or $seconds -is [long])
    $finite=$false;$inRange=$false;$reportedSeconds=$null;$reportedFlag=$null
    if ($flag -is [bool]) {$reportedFlag=$flag}
    if ($numeric) {
        $finite=(-not [double]::IsNaN([double]$seconds) -and -not [double]::IsInfinity([double]$seconds))
        if ($finite) {
            $inRange=($seconds -ge 0 -and $seconds -lt 300)
            if ($seconds -ge 0 -and $seconds -le 1000000) {$reportedSeconds=$seconds}
        }
    }
    $reason='NONE'
    if ($flag -isnot [bool] -or -not $flag) {$reason='LATE_LEASE_FLAG_INVALID'}
    elseif (-not $numeric) {$reason='PREPARATION_SECONDS_TYPE_INVALID'}
    elseif (-not $finite) {$reason='PREPARATION_SECONDS_NOT_FINITE'}
    elseif (-not $inRange) {$reason='PREPARATION_SECONDS_OUT_OF_RANGE'}
    $script:PrivateLateResultDiagnostic=[pscustomobject]@{reason=$reason;late_lease_flag=$reportedFlag;preparation_seconds_type=$typeName;preparation_seconds=$reportedSeconds;preparation_limit_seconds=300;signing_lease_limit_seconds=75}
    if ($reason -cne 'NONE') {throw 'PRIVATE_LATE_LEASE_RESULT_INVALID'}
}
function Find-PriorPrivateV2Failure([object]$Config) {
    $base=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
    $folders=@(Get-ChildItem -LiteralPath $base -Directory -Filter 'copy-private-week-v2-20261116-*')
    if ($folders.Count -gt 32) {throw 'PRIVATE_V2_PRIOR_SEARCH_LIMIT'}
    $found=@()
    foreach ($folder in $folders) {
        if (($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {throw 'PRIVATE_V2_PRIOR_PATH_INVALID'}
        $path=Join-Path $folder.FullName 'FAILURE.json'
        if ([IO.File]::Exists($path)) {
            Assert-PrivateBackupDirectory $folder.FullName;Assert-PrivateBackupFiles $folder.FullName
            $value=Read-RestoreJson $path
            if ($value.retained_diagnostic_helper_id -ceq $Config.prior_private_v2_reader_id) {$found+=,$path}
        }
    }
    if ($found.Count -ne 1) {throw 'PRIVATE_V2_PRIOR_NOT_UNIQUE'}
    return $found[0]
}
function Assert-PriorPrivateV2Stopped([object]$Config,[object]$State) {
    if ($Config.prior_private_v2_reader_id -cnotmatch '^[0-9a-f]{64}$') {throw 'PRIVATE_V2_PRIOR_ID_INVALID'}
    $path=Find-PriorPrivateV2Failure $Config
    $handle=New-Object IO.FileStream($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        $value=Read-RestoreJson $path
        if ($value.failure_category -cne 'PRIVATE_LATE_LEASE_RESULT_INVALID' -or $value.retained_diagnostic_helper_id -cne $Config.prior_private_v2_reader_id -or $value.clone_stopped -isnot [bool] -or -not $value.clone_stopped -or $value.private_key_session_destroyed -isnot [bool] -or -not $value.private_key_session_destroyed -or $value.automatic_retry -isnot [bool] -or $value.automatic_retry -or @($value.retained_helper_ids).Count -ne 2 -or @($value.private_helper_ids).Count -ne 3 -or $value.private_helper_ids[2] -cne $Config.prior_private_v2_reader_id) {throw 'PRIVATE_V2_PRIOR_RECORD_INVALID'}
        $ids=@($value.retained_helper_ids)+@($value.private_helper_ids)
        foreach ($id in $ids) {if (@($Config.prior_private_helper_ids) -ccontains $id) {throw 'PRIVATE_V2_PRIOR_ID_ALIASED'}}
        $prior=[pscustomobject]@{prior_private_helper_ids=$ids;api_container_id=$Config.api_container_id;database_container_id=$Config.database_container_id}
        Assert-PriorPrivateStopped $prior $State
        $script:PriorPrivateV2StoppedVerified=$true
    } finally {$handle.Dispose()}
}
function Read-PrivateSources([object]$Config,[object]$Receipt,[object]$State) {
    $items=Read-PrivateSourcesV2 $Config $Receipt $State
    Assert-PriorPrivateV2Stopped $Config $State
    return ,$items
}
