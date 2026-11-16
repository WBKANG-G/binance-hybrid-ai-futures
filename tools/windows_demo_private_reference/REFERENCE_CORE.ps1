$script:ReferenceAttemptPath=$null
function Reference-AssertPrivateDirectory([string]$Path) {
    Receipt-Require ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) 'REFERENCE_WINDOWS_ACL_REQUIRED'
    Receipt-Require (([IO.File]::GetAttributes($Path) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'REFERENCE_REPARSE_POINT'
    $acl=[IO.Directory]::GetAccessControl($Path);$user=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    Receipt-Require ($acl.AreAccessRulesProtected -and $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ceq $user) 'REFERENCE_ACL_INVALID'
    $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    Receipt-Require ($rules.Count -eq 2) 'REFERENCE_ACL_INVALID'
    foreach ($rule in $rules) {Receipt-Require (@($user,'S-1-5-18') -ccontains $rule.IdentityReference.Value -and $rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and $rule.FileSystemRights -eq [Security.AccessControl.FileSystemRights]::FullControl -and -not $rule.IsInherited) 'REFERENCE_ACL_INVALID'}
}
function Reference-NewDirectory([string]$Token) {
    Receipt-Require ($Token -cmatch '^[0-9a-f]{32}$' -and [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) 'REFERENCE_WINDOWS_ACL_REQUIRED'
    $base=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    Receipt-Require ([IO.Path]::IsPathRooted($base) -and [string]::Equals($base,$env:LOCALAPPDATA,[StringComparison]::OrdinalIgnoreCase)) 'REFERENCE_LOCALAPPDATA_INVALID'
    $drive=New-Object IO.DriveInfo([IO.Path]::GetPathRoot($base))
    Receipt-Require ($drive.DriveFormat -ceq 'NTFS' -and $drive.AvailableFreeSpace -ge 1MB) 'REFERENCE_PRIVATE_STORAGE_UNAVAILABLE'
    $ancestor=New-Object IO.DirectoryInfo($base)
    while ($null -ne $ancestor) {Receipt-Require (($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'REFERENCE_REPARSE_POINT';$ancestor=$ancestor.Parent}
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$system=New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl=New-Object Security.AccessControl.DirectorySecurity;$acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false)
    foreach ($identity in @($sid,$system)) {
        $rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,[Security.AccessControl.FileSystemRights]::FullControl,([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit),[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $null=$acl.AddAccessRule($rule)
    }
    $parent=Join-Path $base 'WBKANG-DEMO-References'
    if (-not [IO.Directory]::Exists($parent)) {$null=[IO.Directory]::CreateDirectory($parent,$acl)}
    Reference-AssertPrivateDirectory $parent
    $path=Join-Path $parent ('v3-reference-'+$Token)
    Receipt-Require (-not [IO.Directory]::Exists($path) -and -not [IO.File]::Exists($path)) 'REFERENCE_DESTINATION_EXISTS'
    $script:ReferenceAttemptPath=$path
    $null=[IO.Directory]::CreateDirectory($path,$acl);Reference-AssertPrivateDirectory $path
    return $path
}
function Reference-WriteNewJson([string]$Path,[object]$Value) {
    $bytes=[Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 16 -Compress)+"`n")
    Receipt-Require ($bytes.Length -le 32768) 'REFERENCE_SIZE_LIMIT'
    $stream=New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)} finally {$stream.Dispose()}
}
function Reference-CheckSource([object[]]$Opened) {
    foreach ($item in $Opened) {
        Receipt-Require ($item.handle.Length -eq $item.body.Length) 'REFERENCE_SOURCE_CHANGED'
        $item.handle.Position=0;$body=New-Object byte[] ($item.body.Length);$offset=0
        while ($offset -lt $body.Length) {$count=$item.handle.Read($body,$offset,$body.Length-$offset);Receipt-Require ($count -gt 0) 'REFERENCE_SOURCE_CHANGED';$offset+=$count}
        Receipt-Require ($item.handle.Length -eq $item.body.Length -and (Receipt-Digest $body) -ceq $item.sha256) 'REFERENCE_SOURCE_CHANGED'
    }
}
function Invoke-PrivateReference([string]$Base,[object]$Config,[switch]$CheckOnly,[switch]$CreateReference) {
    $opened=@();$targets=@();$destination=$null;$script:ReferenceAttemptPath=$null
    try {
        Receipt-Require (($CheckOnly -and -not $CreateReference) -or ($CreateReference -and -not $CheckOnly)) 'REFERENCE_EXPLICIT_MODE_REQUIRED'
        $review=Review-PrivateReceipts $Base $Config
        if ($CheckOnly) {
            return [pscustomobject]@{private_reference='SOURCE_RECEIPTS_VERIFIED_NO_REFERENCE_CREATED';source_receipt_sha256=$review.receipt_sha256;source_records_checked=5;reference_created=$false;source_files_changed=$false;docker_used=$false;services_started=$false;external_copy_performed=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
        }
        Receipt-Require ($review.selected_directory_name -cmatch '^copy-private-week-v3-20261116-[0-9a-f]{32}$') 'REFERENCE_SOURCE_INVALID'
        $source=Join-Path $Base $review.selected_directory_name
        $names=@('COPY_PRIVATE_WEEK_V3_RECEIPT.json','PRIVATE_WEEK_RESULT.json','PRIVATE_RESOURCES.json','RESTORE_RESOURCES.json','CLONE_CREATED.json')
        foreach ($name in $names) {
            $item=Receipt-Open (Join-Path $source $name);$opened+=,$item
            Receipt-Require ($item.sha256 -ceq $review.file_sha256.$name) 'REFERENCE_SOURCE_CHANGED'
        }
        Reference-CheckSource $opened
        $token=[Guid]::NewGuid().ToString('N');$destination=Reference-NewDirectory $token
        $manifest=[ordered]@{policy='PRIVATE_V3_HASH_REFERENCE_ONLY_V1';reference_id=$token;created_at_utc=[DateTime]::UtcNow.ToString('o');created_by_source_reference=$Config.reference_builder_source_reference;producer_source_reference=$Config.expected_source_reference;source_directory_name=$review.selected_directory_name;source_receipt_sha256=$review.receipt_sha256;source_file_sha256=$review.file_sha256;prior_reference_pins=[ordered]@{backup_manifest_sha256=$Config.manifest_sha256;backup_receipt_sha256=$Config.receipt_sha256;copy_receipt_sha256=$Config.prior_copy_receipt_sha256;clock_receipt_sha256=$Config.prior_clock_receipt_sha256;evidence_receipt_sha256=$Config.prior_evidence_receipt_sha256};recorded_facts=[ordered]@{private_preparation_seconds=$review.private_preparation_seconds;recorded_preparation_numeric_type=$review.recorded_preparation_numeric_type;private_lease_opened_at_capture=$true;signing_lease_limit_seconds=75;recorded_key_session_destroyed=$true;recorded_clone_stopped=$true;orders_preserved=34;unresolved_requests_preserved=4;stored_day_kst='2026-11-13';stored_week_kst='2026-11-09';content_requirements_remaining=8};contains_original_backup_data=$false;contains_source_receipt_contents=$false;contains_key_material=$false;external_copy_performed=$false;independent_external_custody_verified=$false;current_docker_state_checked=$false;archive_response_bytes_replayed=$false;remote_account_identity_verified=$false;baseline_adoption_approved=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
        Reference-WriteNewJson (Join-Path $destination 'REFERENCE_MANIFEST.json') $manifest
        $saved=Receipt-Open (Join-Path $destination 'REFERENCE_MANIFEST.json');$targets+=,$saved
        Receipt-Require (Receipt-SameObject $saved.value ($manifest|ConvertTo-Json -Depth 16 -Compress|ConvertFrom-Json)) 'REFERENCE_READBACK_MISMATCH'
        Reference-CheckSource $opened
        $receipt=[ordered]@{policy='LOCAL_PRIVATE_REFERENCE_RECEIPT_V1';status='LOCAL_REFERENCE_VERIFIED_EXTERNAL_CUSTODY_PENDING';reference_id=$token;manifest_sha256=$saved.sha256;source_receipt_sha256=$review.receipt_sha256;source_files_reverified=$true;original_source_files_written=$false;reference_only_not_recovery_backup=$true;external_copy_performed=$false;independent_external_custody_verified=$false;real_credentials_accessed=$false;docker_used=$false;services_started=$false;automatic_retry=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
        Reference-WriteNewJson (Join-Path $destination 'REFERENCE_RECEIPT.json') $receipt
        $savedReceipt=Receipt-Open (Join-Path $destination 'REFERENCE_RECEIPT.json');$targets+=,$savedReceipt
        Receipt-Require (Receipt-SameObject $savedReceipt.value ($receipt|ConvertTo-Json -Depth 16 -Compress|ConvertFrom-Json)) 'REFERENCE_READBACK_MISMATCH'
        Reference-CheckSource $opened;Reference-CheckSource $targets
        return [pscustomobject]@{private_reference='LOCAL_REFERENCE_VERIFIED_EXTERNAL_CUSTODY_PENDING';private_reference_directory=$destination;reference_manifest_sha256=$saved.sha256;reference_receipt_sha256=$savedReceipt.sha256;source_receipt_sha256=$review.receipt_sha256;source_records_checked=5;reference_file_count=2;reference_created=$true;reference_only_not_recovery_backup=$true;source_files_changed=$false;source_receipt_contents_copied=$false;key_material_included=$false;docker_used=$false;services_started=$false;external_copy_performed=$false;independent_external_custody_verified=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } catch {
        if ($null -eq $destination -and $null -ne $script:ReferenceAttemptPath) {$destination=$script:ReferenceAttemptPath}
        $reason='REFERENCE_UNAVAILABLE';if ($_.Exception.Message -cmatch '^(REFERENCE|RECEIPT)_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
        return [pscustomobject]@{private_reference='BLOCKED_NO_AUTOMATIC_RETRY';failure_category=$reason;retained_partial_reference_directory=$destination;reference_creation_may_have_started=($null -ne $destination);source_files_written=$false;source_unchanged_reverified=$false;source_integrity_recheck_failed=(@('REFERENCE_SOURCE_CHANGED','RECEIPT_CHANGED_DURING_REVIEW') -ccontains $reason);docker_used=$false;services_started=$false;external_copy_performed=$false;automatic_retry=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } finally {foreach ($item in @($opened+$targets)) {$item.handle.Dispose()}}
}
