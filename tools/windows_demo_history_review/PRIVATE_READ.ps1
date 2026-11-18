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
