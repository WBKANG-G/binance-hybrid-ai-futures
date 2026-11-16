function Reference-AssertPrivateDirectory([string]$Path) {
    Receipt-Require ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) 'REFERENCE_WINDOWS_ACL_REQUIRED'
    Receipt-Require (([IO.File]::GetAttributes($Path) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'REFERENCE_REPARSE_POINT'
    $acl=[IO.Directory]::GetAccessControl($Path);$user=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    Receipt-Require ($acl.AreAccessRulesProtected -and $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ceq $user) 'REFERENCE_ACL_INVALID'
    $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    Receipt-Require ($rules.Count -eq 2) 'REFERENCE_ACL_INVALID'
    foreach ($rule in $rules) {Receipt-Require (@($user,'S-1-5-18') -ccontains $rule.IdentityReference.Value -and $rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and $rule.FileSystemRights -eq [Security.AccessControl.FileSystemRights]::FullControl -and -not $rule.IsInherited) 'REFERENCE_ACL_INVALID'}
}
function Handoff-ExactFields([object]$Value,[string[]]$Names) {
    Receipt-Require ((Receipt-Object $Value) -and (@($Value.PSObject.Properties.Name|Sort-Object) -join '|') -ceq (@($Names|Sort-Object) -join '|')) 'HANDOFF_REFERENCE_SCHEMA_INVALID'
}
function Handoff-CheckReference([object]$Manifest,[object]$Receipt,[object]$Config,[object]$Review,[string]$ManifestHash,[string]$DirectoryName) {
    Handoff-ExactFields $Manifest @('policy','reference_id','created_at_utc','created_by_source_reference','producer_source_reference','source_directory_name','source_receipt_sha256','source_file_sha256','prior_reference_pins','recorded_facts','contains_original_backup_data','contains_source_receipt_contents','contains_key_material','external_copy_performed','independent_external_custody_verified','current_docker_state_checked','archive_response_bytes_replayed','remote_account_identity_verified','baseline_adoption_approved','execution_approved','orders_enabled','live_trading_enabled')
    Handoff-ExactFields $Receipt @('policy','status','reference_id','manifest_sha256','source_receipt_sha256','source_files_reverified','original_source_files_written','reference_only_not_recovery_backup','external_copy_performed','independent_external_custody_verified','real_credentials_accessed','docker_used','services_started','automatic_retry','execution_approved','orders_enabled','live_trading_enabled')
    Receipt-Require ($DirectoryName -cmatch '^v3-reference-[0-9a-f]{32}$' -and $Manifest.reference_id -ceq $DirectoryName.Substring(13) -and $Receipt.reference_id -ceq $Manifest.reference_id -and $Manifest.policy -ceq 'PRIVATE_V3_HASH_REFERENCE_ONLY_V1' -and $Manifest.created_by_source_reference -ceq $Config.expected_reference_source_reference -and $Manifest.producer_source_reference -ceq $Config.expected_source_reference -and $Manifest.source_directory_name -ceq $Review.selected_directory_name -and $Manifest.source_receipt_sha256 -ceq $Review.receipt_sha256 -and $Receipt.source_receipt_sha256 -ceq $Review.receipt_sha256 -and $Receipt.manifest_sha256 -ceq $ManifestHash -and $Receipt.policy -ceq 'LOCAL_PRIVATE_REFERENCE_RECEIPT_V1' -and $Receipt.status -ceq 'LOCAL_REFERENCE_VERIFIED_EXTERNAL_CUSTODY_PENDING') 'HANDOFF_REFERENCE_LINK_INVALID'
    if ($Manifest.created_at_utc -is [datetime]) {$stamp=$Manifest.created_at_utc.ToUniversalTime().ToString('o')} else {$stamp=$Manifest.created_at_utc}
    Receipt-Require ($stamp -is [string] -and $stamp -cmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z$') 'HANDOFF_REFERENCE_TIME_INVALID'
    Receipt-Flags $Manifest @('contains_original_backup_data','contains_source_receipt_contents','contains_key_material','external_copy_performed','independent_external_custody_verified','current_docker_state_checked','archive_response_bytes_replayed','remote_account_identity_verified','baseline_adoption_approved','execution_approved','orders_enabled','live_trading_enabled') $false
    Receipt-Flags $Receipt @('source_files_reverified','reference_only_not_recovery_backup') $true
    Receipt-Flags $Receipt @('original_source_files_written','external_copy_performed','independent_external_custody_verified','real_credentials_accessed','docker_used','services_started','automatic_retry','execution_approved','orders_enabled','live_trading_enabled') $false
    $names=@('COPY_PRIVATE_WEEK_V3_RECEIPT.json','PRIVATE_WEEK_RESULT.json','PRIVATE_RESOURCES.json','RESTORE_RESOURCES.json','CLONE_CREATED.json')
    Handoff-ExactFields $Manifest.source_file_sha256 $names
    foreach ($name in $names) {Receipt-Require ($Manifest.source_file_sha256.$name -cmatch '^[0-9a-f]{64}$' -and $Manifest.source_file_sha256.$name -ceq $Review.file_sha256.$name) 'HANDOFF_SOURCE_REFERENCE_CHANGED'}
    Handoff-ExactFields $Manifest.prior_reference_pins @('backup_manifest_sha256','backup_receipt_sha256','copy_receipt_sha256','clock_receipt_sha256','evidence_receipt_sha256')
    $pins=$Manifest.prior_reference_pins
    Receipt-Require ($pins.backup_manifest_sha256 -ceq $Config.manifest_sha256 -and $pins.backup_receipt_sha256 -ceq $Config.receipt_sha256 -and $pins.copy_receipt_sha256 -ceq $Config.prior_copy_receipt_sha256 -and $pins.clock_receipt_sha256 -ceq $Config.prior_clock_receipt_sha256 -and $pins.evidence_receipt_sha256 -ceq $Config.prior_evidence_receipt_sha256) 'HANDOFF_REFERENCE_PINS_INVALID'
    $facts=$Manifest.recorded_facts
    Handoff-ExactFields $facts @('private_preparation_seconds','recorded_preparation_numeric_type','private_lease_opened_at_capture','signing_lease_limit_seconds','recorded_key_session_destroyed','recorded_clone_stopped','orders_preserved','unresolved_requests_preserved','stored_day_kst','stored_week_kst','content_requirements_remaining')
    Receipt-Require ((Receipt-Numeric $facts.private_preparation_seconds) -and [decimal]$facts.private_preparation_seconds -eq [decimal]$Review.private_preparation_seconds -and $facts.recorded_preparation_numeric_type -ceq 'System.Decimal' -and (Receipt-Integer $facts.signing_lease_limit_seconds 75) -and (Receipt-Integer $facts.orders_preserved 34) -and (Receipt-Integer $facts.unresolved_requests_preserved 4) -and (Receipt-Integer $facts.content_requirements_remaining 8) -and $facts.stored_day_kst -ceq '2026-11-13' -and $facts.stored_week_kst -ceq '2026-11-09') 'HANDOFF_REFERENCE_FACTS_INVALID'
    Receipt-Flags $facts @('private_lease_opened_at_capture','recorded_key_session_destroyed','recorded_clone_stopped') $true
}
function Find-LocalReferenceBase {return (Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-References')}
function Handoff-SelectReference([object]$Config,[object]$Review,[System.Collections.ArrayList]$Handles) {
    $base=Find-LocalReferenceBase
    Reference-AssertPrivateDirectory $base
    $folders=@(Get-ChildItem -LiteralPath $base -Directory -Filter 'v3-reference-*')
    Receipt-Require ($folders.Count -le 32) 'HANDOFF_REFERENCE_SEARCH_LIMIT'
    $found=@()
    foreach ($folder in $folders) {
        Receipt-Require ($folder.Name -cmatch '^v3-reference-[0-9a-f]{32}$' -and ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'HANDOFF_REFERENCE_DIRECTORY_INVALID'
        Reference-AssertPrivateDirectory $folder.FullName
        $names=@(Get-ChildItem -LiteralPath $folder.FullName -Force)
        Receipt-Require ($names.Count -le 2) 'HANDOFF_REFERENCE_EXTRA_FILES'
        if (-not [IO.File]::Exists((Join-Path $folder.FullName 'REFERENCE_RECEIPT.json'))) {continue}
        Receipt-Require ($names.Count -eq 2) 'HANDOFF_REFERENCE_FILES_INVALID'
        foreach ($file in $names) {Receipt-Require (-not $file.PSIsContainer -and ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -and @('REFERENCE_MANIFEST.json','REFERENCE_RECEIPT.json') -ccontains $file.Name -and $file.Length -le 32768) 'HANDOFF_REFERENCE_FILES_INVALID'}
        $m=Receipt-Open (Join-Path $folder.FullName 'REFERENCE_MANIFEST.json');$null=$Handles.Add($m)
        $r=Receipt-Open (Join-Path $folder.FullName 'REFERENCE_RECEIPT.json');$null=$Handles.Add($r)
        if ($m.value.created_by_source_reference -ceq $Config.expected_reference_source_reference -and $m.value.source_receipt_sha256 -ceq $Review.receipt_sha256) {
            Handoff-CheckReference $m.value $r.value $Config $Review $m.sha256 $folder.Name
            $found+=,[pscustomobject]@{directory=$folder.FullName;manifest=$m;receipt=$r}
        }
    }
    Receipt-Require ($found.Count -eq 1) 'HANDOFF_REFERENCE_NOT_UNIQUE'
    return $found[0]
}
function Handoff-Recheck([object[]]$Items) {
    foreach ($item in $Items) {
        Receipt-Require ($item.handle.Length -eq $item.body.Length) 'HANDOFF_FILE_CHANGED'
        $item.handle.Position=0;$bytes=New-Object byte[] ($item.body.Length);$offset=0
        while ($offset -lt $bytes.Length) {$n=$item.handle.Read($bytes,$offset,$bytes.Length-$offset);Receipt-Require ($n -gt 0) 'HANDOFF_FILE_CHANGED';$offset+=$n}
        Receipt-Require ($item.handle.Length -eq $item.body.Length -and (Receipt-Digest $bytes) -ceq $item.sha256) 'HANDOFF_FILE_CHANGED'
    }
}
function Handoff-AssertMediaSame([object]$Before,[object]$After) {
    Assert-HandoffMediaFacts $After
    Receipt-Require ($Before.identity_sha256 -ceq $After.identity_sha256 -and $Before.filesystem -ceq $After.filesystem -and $Before.root -ceq $After.root) 'HANDOFF_MEDIA_CHANGED'
}
function Handoff-NewDirectory([object]$Media,[string]$Token) {
    Assert-HandoffMediaFacts $Media
    Receipt-Require ($Token -cmatch '^[0-9a-f]{32}$') 'HANDOFF_DESTINATION_INVALID'
    Receipt-Require (([IO.File]::GetAttributes($Media.root) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'HANDOFF_REPARSE_POINT'
    $path=Join-Path $Media.root ('WBKANG-DEMO-Reference-20261116-'+$Token)
    Receipt-Require (-not [IO.Directory]::Exists($path) -and -not [IO.File]::Exists($path)) 'HANDOFF_DESTINATION_EXISTS'
    $script:HandoffAttemptPath=$path
    if ($Media.filesystem -ceq 'NTFS') {
        $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$system=New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
        $acl=New-Object Security.AccessControl.DirectorySecurity;$acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false)
        foreach ($id in @($sid,$system)) {$rule=New-Object Security.AccessControl.FileSystemAccessRule($id,[Security.AccessControl.FileSystemRights]::FullControl,([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit),[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow);$null=$acl.AddAccessRule($rule)}
        $null=[IO.Directory]::CreateDirectory($path,$acl)
    } else {$null=[IO.Directory]::CreateDirectory($path)}
    Handoff-AssertDestination $path $Media.filesystem
    Receipt-Require (@(Get-ChildItem -LiteralPath $path -Force).Count -eq 0) 'HANDOFF_DESTINATION_NOT_EMPTY'
    return $path
}
function Handoff-AssertDestination([string]$Path,[string]$Filesystem) {
    $item=Get-Item -LiteralPath $Path
    Receipt-Require ($item.PSIsContainer -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'HANDOFF_DESTINATION_INVALID'
    foreach ($entry in @(Get-ChildItem -LiteralPath $Path -Force)) {Receipt-Require (-not $entry.PSIsContainer -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -and @('REFERENCE_MANIFEST.json','REFERENCE_RECEIPT.json','REFERENCE_HANDOFF_RECEIPT.json') -ccontains $entry.Name) 'HANDOFF_DESTINATION_CONTENT_INVALID'}
    if ($Filesystem -ceq 'NTFS') {Reference-AssertPrivateDirectory $Path}
}
function Handoff-WriteNew([string]$Path,[byte[]]$Bytes) {
    Receipt-Require ($Bytes.Length -le 32768) 'HANDOFF_SIZE_LIMIT'
    $stream=New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try {$stream.Write($Bytes,0,$Bytes.Length);$stream.Flush($true)} finally {$stream.Dispose()}
}
function Invoke-ReferenceHandoff([string]$Base,[object]$Config,[switch]$CheckOnly,[switch]$CopyReference) {
    $handles=New-Object Collections.ArrayList;$targets=@();$destination=$null;$stage='MODE';$script:HandoffAttemptPath=$null
    try {
        Receipt-Require (($CheckOnly -and -not $CopyReference) -or ($CopyReference -and -not $CheckOnly)) 'HANDOFF_EXPLICIT_MODE_REQUIRED'
        $stage='SOURCE_RECEIPTS_AND_LOCAL_REFERENCE';$review=Review-PrivateReceipts $Base $Config
        $sourceNames=@('COPY_PRIVATE_WEEK_V3_RECEIPT.json','PRIVATE_WEEK_RESULT.json','PRIVATE_RESOURCES.json','RESTORE_RESOURCES.json','CLONE_CREATED.json')
        foreach ($name in $sourceNames) {$opened=Receipt-Open (Join-Path (Join-Path $Base $review.selected_directory_name) $name);$null=$handles.Add($opened);Receipt-Require ($opened.sha256 -ceq $review.file_sha256.$name) 'HANDOFF_SOURCE_REFERENCE_CHANGED'}
        $selected=Handoff-SelectReference $Config $review $handles
        $source=$selected.directory;$items=@($selected.manifest,$selected.receipt)
        $total=[long]($items[0].body.Length+$items[1].body.Length)
        $stage='D_USB_IDENTITY';$media=Read-HandoffMedia $Config.destination_drive;Assert-HandoffMediaFacts $media
        Receipt-Require ($media.free_bytes -ge ($total+1MB)) 'HANDOFF_SPACE_INSUFFICIENT'
        Handoff-Recheck @($handles.ToArray())
        if ($CheckOnly) {return [pscustomobject]@{reference_handoff='SOURCE_REFERENCE_AND_USB_CHECKED_NO_COPY';destination_drive='D:';filesystem=$media.filesystem;bus=$media.bus;file_count=2;bytes_to_copy=$total;source_receipt_sha256=$review.receipt_sha256;reference_manifest_sha256=$items[0].sha256;reference_receipt_sha256=$items[1].sha256;copy_performed=$false;docker_used=$false;services_started=$false;physical_disconnect_verified=$false;independent_external_custody_verified=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}}
        $stage='NEW_USB_DIRECTORY';Handoff-AssertMediaSame $media (Read-HandoffMedia $Config.destination_drive)
        $destination=Handoff-NewDirectory $media ([Guid]::NewGuid().ToString('N'))
        $names=@('REFERENCE_MANIFEST.json','REFERENCE_RECEIPT.json')
        for ($i=0;$i -lt 2;$i++) {
            $stage='COPY_HASH_REFERENCE';Handoff-AssertMediaSame $media (Read-HandoffMedia $Config.destination_drive);Handoff-Recheck @($handles.ToArray())
            Handoff-WriteNew (Join-Path $destination $names[$i]) $items[$i].body
            $readback=Receipt-Open (Join-Path $destination $names[$i]);$targets+=,$readback
            Receipt-Require ($readback.sha256 -ceq $items[$i].sha256) 'HANDOFF_COPY_HASH_MISMATCH'
            Handoff-AssertDestination $destination $media.filesystem
        }
        $stage='VERIFY_BOTH_COPIES';Handoff-Recheck @($handles.ToArray());Handoff-Recheck $targets
        Handoff-AssertMediaSame $media (Read-HandoffMedia $Config.destination_drive)
        $record=[ordered]@{policy='PRIVATE_REFERENCE_USB_HANDOFF_V1';status='EXTERNAL_REFERENCE_HASHES_VERIFIED_SAFE_EJECT_PENDING';handoff_source_reference=$Config.handoff_source_reference;local_reference_id=$selected.manifest.value.reference_id;source_receipt_sha256=$review.receipt_sha256;reference_manifest_sha256=$items[0].sha256;reference_receipt_sha256=$items[1].sha256;media_identity_sha256=$media.identity_sha256;destination_filesystem=$media.filesystem;destination_ntfs_acl_verified=($media.filesystem -ceq 'NTFS');copied_reference_file_count=2;contains_original_backup_data=$false;contains_key_material=$false;docker_used=$false;services_started=$false;physical_disconnect_verified=$false;independent_external_custody_verified=$false;separate_pc_verification_performed=$false;automatic_retry=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
        Handoff-WriteNew (Join-Path $destination 'REFERENCE_HANDOFF_RECEIPT.json') ([Text.Encoding]::UTF8.GetBytes(($record|ConvertTo-Json -Depth 16 -Compress)+"`n"))
        $receipt=Receipt-Open (Join-Path $destination 'REFERENCE_HANDOFF_RECEIPT.json');$targets+=,$receipt
        Receipt-Require (Receipt-SameObject $receipt.value ($record|ConvertTo-Json -Depth 16 -Compress|ConvertFrom-Json)) 'HANDOFF_RECEIPT_READBACK_MISMATCH'
        Handoff-Recheck @($handles.ToArray());Handoff-Recheck $targets;Handoff-AssertMediaSame $media (Read-HandoffMedia $Config.destination_drive);Handoff-AssertDestination $destination $media.filesystem
        return [pscustomobject]@{reference_handoff='EXTERNAL_REFERENCE_HASHES_VERIFIED_SAFE_EJECT_PENDING';destination_directory=$destination;destination_filesystem=$media.filesystem;destination_ntfs_acl_verified=($media.filesystem -ceq 'NTFS');reference_manifest_sha256=$items[0].sha256;reference_receipt_sha256=$items[1].sha256;handoff_receipt_sha256=$receipt.sha256;source_receipt_sha256=$review.receipt_sha256;copied_reference_file_count=2;new_destination_file_count=3;file_hashes_verified=$true;source_files_written=$false;existing_backup_files_written=$false;contains_original_backup_data=$false;contains_key_material=$false;docker_used=$false;services_started=$false;physical_disconnect_verified=$false;independent_external_custody_verified=$false;separate_pc_verification_performed=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } catch {
        if ($null -eq $destination -and $null -ne $script:HandoffAttemptPath) {$destination=$script:HandoffAttemptPath}
        $reason='HANDOFF_UNAVAILABLE';if ($_.Exception.Message -cmatch '^(HANDOFF|REFERENCE|RECEIPT)_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
        return [pscustomobject]@{reference_handoff='BLOCKED_NO_AUTOMATIC_RETRY';stage=$stage;failure_category=$reason;retained_partial_destination=$destination;copy_completed=$false;source_files_written=$false;existing_backup_files_written=$false;docker_used=$false;services_started=$false;automatic_retry=$false;physical_disconnect_verified=$false;independent_external_custody_verified=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
    } finally {foreach ($item in $handles) {$item.handle.Dispose()};foreach ($item in $targets) {$item.handle.Dispose()}}
}
