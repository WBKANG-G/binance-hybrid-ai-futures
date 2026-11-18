$script:ReferenceAttemptPath=$null
function Reference-AssertPrivateDirectory([string]$Path) {
    Inspect-Require ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) 'REFERENCE_WINDOWS_ACL_REQUIRED'
    Inspect-Require (([IO.File]::GetAttributes($Path) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'REFERENCE_REPARSE_POINT'
    $acl=[IO.Directory]::GetAccessControl($Path);$user=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    Inspect-Require ($acl.AreAccessRulesProtected -and $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ceq $user) 'REFERENCE_ACL_INVALID'
    $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
    Inspect-Require ($rules.Count -eq 2) 'REFERENCE_ACL_INVALID'
    foreach ($rule in $rules) {Inspect-Require (@($user,'S-1-5-18') -ccontains $rule.IdentityReference.Value -and $rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and $rule.FileSystemRights -eq [Security.AccessControl.FileSystemRights]::FullControl -and -not $rule.IsInherited) 'REFERENCE_ACL_INVALID'}
}
function Reference-NewDirectory([string]$Token) {
    Inspect-Require ($Token -cmatch '^[0-9a-f]{32}$' -and [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) 'REFERENCE_WINDOWS_ACL_REQUIRED'
    $base=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    Inspect-Require ([IO.Path]::IsPathRooted($base) -and [string]::Equals($base,$env:LOCALAPPDATA,[StringComparison]::OrdinalIgnoreCase)) 'REFERENCE_LOCALAPPDATA_INVALID'
    $drive=New-Object IO.DriveInfo([IO.Path]::GetPathRoot($base))
    Inspect-Require ($drive.DriveFormat -ceq 'NTFS' -and $drive.AvailableFreeSpace -ge 1MB) 'REFERENCE_PRIVATE_STORAGE_UNAVAILABLE'
    $ancestor=New-Object IO.DirectoryInfo($base)
    while ($null -ne $ancestor) {Inspect-Require (($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'REFERENCE_REPARSE_POINT';$ancestor=$ancestor.Parent}
    $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$system=New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl=New-Object Security.AccessControl.DirectorySecurity;$acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false)
    foreach ($identity in @($sid,$system)) {
        $rule=New-Object Security.AccessControl.FileSystemAccessRule($identity,[Security.AccessControl.FileSystemRights]::FullControl,([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit),[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $null=$acl.AddAccessRule($rule)
    }
    $parent=Join-Path $base 'WBKANG-DEMO-CurrentReads'
    if (-not [IO.Directory]::Exists($parent)) {$null=[IO.Directory]::CreateDirectory($parent,$acl)}
    Reference-AssertPrivateDirectory $parent
    $path=Join-Path $parent ('current-read-20261118-'+$Token)
    Inspect-Require (-not [IO.Directory]::Exists($path) -and -not [IO.File]::Exists($path)) 'REFERENCE_DESTINATION_EXISTS'
    $script:ReferenceAttemptPath=$path
    $null=[IO.Directory]::CreateDirectory($path,$acl);Reference-AssertPrivateDirectory $path
    return $path
}
