$script:ReviewedRestoreHandle=$null
function Read-RestoreSources([object]$Config,[object]$Receipt,[object]$State) {
 $path=Find-RestoreEvidence $Config
 $handle=New-Object IO.FileStream($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try {
  if((Restore-Hash $path) -cne $Config.restore_receipt_sha256){throw 'HANDOFF_RESTORE_HASH_MISMATCH'}
  $recovery=Read-RestoreJson $path
  Assert-RestoreEvidence $recovery $Config
  $items=Assert-HandoffStoppedScope $Config $Receipt $State $recovery
  # The old scope guard checked bind type/RO; additionally bind it to the pinned backup directory.
  $backup=Find-RetainedBackup $Config
  $worker=@($items|Where-Object {$_.id -ceq $recovery.retained_helper_ids[0]})
  if($worker.Count -ne 1){throw 'HANDOFF_HELPER_CONFIGURATION_CHANGED'}
  $bind=@($worker[0].mounts|Where-Object {$_.Destination -ceq '/bundle'})
  if($bind.Count -ne 1 -or $bind[0].Type -cne 'bind' -or $bind[0].RW -or -not [string]::Equals($bind[0].Source.Replace('/','\').TrimEnd('\'),$backup.Replace('/','\').TrimEnd('\'),[StringComparison]::OrdinalIgnoreCase)){throw 'HANDOFF_RESTORE_BIND_SOURCE_CHANGED'}
  if((Restore-Hash $path) -cne $Config.restore_receipt_sha256){throw 'HANDOFF_RESTORE_HASH_MISMATCH'}
  $script:ReviewedRestoreHandle=$handle
  return ,$items
 } catch {$handle.Dispose();throw}
}
