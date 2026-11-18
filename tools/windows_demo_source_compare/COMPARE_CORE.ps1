function Assert-CompareHelper([string]$Id,[object]$State,[object[]]$Original) {
 if($Id -cnotmatch '^[0-9a-f]{64}$' -or @($Original.id) -ccontains $Id){throw 'COMPARE_HELPER_ID_INVALID'}
 $item=Read-Container $Id
 if($item.image -cne $State.canonical_images[0] -or $item.running -or $item.restarting -or $item.paused -or $item.dead -or $item.restart.Name -cne 'no' -or $item.restart.MaximumRetryCount -ne 0 -or @($item.mounts).Count -ne 2){throw 'COMPARE_HELPER_SCOPE_INVALID'}
 foreach($spec in @(@('/source/database',$State.database_volume),@('/source/runtime',$State.runtime_volume))){
  $mount=@($item.mounts|Where-Object {$_.Destination -ceq $spec[0]})
  if($mount.Count -ne 1 -or $mount[0].Type -cne 'volume' -or $mount[0].Name -cne $spec[1] -or $mount[0].RW){throw 'COMPARE_HELPER_MOUNT_INVALID'}
 }
 $lines=@(Backup-Docker @('inspect','--format','{{json .HostConfig}}{{println}}{{json .Config.User}}{{println}}{{json .Config.Entrypoint}}{{println}}{{json .Config.Cmd}}',$Id) 4)
 if($lines.Count -ne 4){throw 'COMPARE_HELPER_SCOPE_INVALID'}
 $hostConfig=ConvertFrom-Json -InputObject $lines[0]
 $user=ConvertFrom-Json -InputObject ('{"value":'+$lines[1]+'}')
 $entry=ConvertFrom-Json -InputObject ('{"value":'+$lines[2]+'}')
 $command=ConvertFrom-Json -InputObject ('{"value":'+$lines[3]+'}')
 if($hostConfig.NetworkMode -cne 'none' -or -not $hostConfig.ReadonlyRootfs -or $hostConfig.Privileged -or $hostConfig.Memory -ne 536870912 -or $hostConfig.PidsLimit -ne 32 -or (@($hostConfig.CapDrop) -join '|') -cne 'ALL' -or @('DAC_OVERRIDE','CAP_DAC_OVERRIDE') -cnotcontains (@($hostConfig.CapAdd) -join '|') -or (@($hostConfig.SecurityOpt) -join '|') -cne 'no-new-privileges' -or @($hostConfig.Devices).Count -ne 0 -or $user.value -cne '0:0' -or (@($entry.value) -join '|') -cne 'python' -or (@($command.value) -join '|') -cne '-I|-B|-'){throw 'COMPARE_HELPER_ISOLATION_INVALID'}
}
function Invoke-SourceCompare([string]$Root,[switch]$CheckOnly,[switch]$CompareOnce) {
 $handles=@();$helper=$null;$original=@();$mutex=$null;$locked=$false;$stage='INPUTS';$stopped=$false
 try {
  if(([int][bool]$CheckOnly+[int][bool]$CompareOnce) -ne 1){throw 'COMPARE_EXPLICIT_MODE_REQUIRED'}
  $config=Read-RestoreJson (Join-Path $Root 'CONFIG.json')
  $backup=Find-RetainedBackup $config
  $receipt=Read-RestoreJson (Join-Path $backup 'BACKUP_RECEIPT.json')
  Assert-InputHashes $backup $config $receipt
  # Keep every input open read-only during scope admission and byte verification.
  foreach($name in @('BACKUP_RECEIPT.json','BACKUP_STATE.json','manifest.json','database.tar','runtime.tar','images.tar')){$handles+=,(New-Object IO.FileStream((Join-Path $backup $name),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read))}
  Assert-InputHashes $backup $config $receipt
  $state=Read-RestoreJson (Join-Path $backup 'BACKUP_STATE.json')
  $original=Read-RestoreSources $config $receipt $state
  if($CheckOnly){[pscustomobject]@{source_compare='PINNED_INPUTS_AND_STOPPED_SCOPE_CHECKED';source_bytes_compared=$false;helper_created=$false;originals_started=$false;source_data_written=$false;actual_http_attempts=0;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output;Write-Output 'SOURCE_COMPARE_CHECK_PASSED_NO_SOURCE_START';return 0}
  $mutex=New-Object Threading.Mutex($false,'Global\WBKANG_DEMO_COLD_BACKUP');$locked=$mutex.WaitOne(0);if(-not $locked){throw 'COLD_ANOTHER_BACKUP_ACTIVE'}
  Assert-RestoreSourceSnapshot $original @() $false
  $stage='CREATE_READ_ONLY_HELPER'
  $ids=@(Backup-Docker @('create','--pull=never','--interactive','--network','none','--restart','no','--read-only','--memory','512m','--pids-limit','32','--cap-drop','ALL','--cap-add','DAC_OVERRIDE','--security-opt','no-new-privileges','--user','0:0','--mount',('type=volume,source='+$state.database_volume+',target=/source/database,readonly,volume-nocopy'),'--mount',('type=volume,source='+$state.runtime_volume+',target=/source/runtime,readonly,volume-nocopy'),'--entrypoint','python',$state.canonical_images[0],'-I','-B','-') 1)
  if($ids.Count -ne 1 -or $ids[0] -cnotmatch '^[0-9a-f]{64}$'){throw 'COMPARE_HELPER_ID_INVALID'};$helper=$ids[0]
  Assert-CompareHelper $helper $state $original
  Assert-RestoreSourceSnapshot $original @($helper) $false
  $stage='COMPARE_TWO_PASSES'
  $driver=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'worker.py')))
  $library=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'READ_LIBRARY.py')))
  $manifest=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $backup 'manifest.json')))
  $payload="import base64,json`nREAD_LIBRARY=base64.b64decode('$library')`nMANIFEST=json.loads(base64.b64decode('$manifest'))`nexec(compile(base64.b64decode('$driver'),'<source-compare>','exec'))"
  $lines=@($payload|& docker start --attach --interactive $helper 2>$null);$workerExit=$LASTEXITCODE
  Assert-CompareHelper $helper $state $original;$stopped=$true
  Assert-RestoreSourceSnapshot $original @($helper) $false
  if($lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 4096){throw 'COMPARE_WORKER_OUTPUT_INVALID'}
  $value=ConvertFrom-Json -InputObject ([string]$lines[0])
  if($workerExit -ne 0 -or $value.status -cne 'STOPPED_SOURCE_TREES_MATCH_PINNED_BACKUP' -or $value.failure_category -cne 'NONE' -or $value.passes -ne 2 -or $value.source_data_written -isnot [bool] -or $value.source_data_written -or $value.logical_account_row_queried -isnot [bool] -or $value.logical_account_row_queried){throw 'COMPARE_WORKER_NOT_MATCHED'}
  $m=Read-RestoreJson (Join-Path $backup 'manifest.json') 16777216
  foreach($g in @('database','runtime')){if($value.file_counts.$g -ne @($m.groups.$g.entries|Where-Object {$_.kind -ceq 'file'}).Count -or $value.logical_bytes.$g -ne $m.groups.$g.logical_bytes){throw 'COMPARE_WORKER_OUTPUT_INVALID'}}
  Assert-InputHashes $backup $config $receipt
  [pscustomobject]@{source_compare='STOPPED_ORIGINAL_BYTES_AND_METADATA_MATCH_PINNED_BACKUP';manifest_sha256=$config.manifest_sha256;passes=2;file_counts=$value.file_counts;helper_container_id=$helper;helper_stopped_verified=$true;source_data_written=$false;originals_started=$false;logical_account_row_queried=$false;current_database_logical_state_verified=$false;backup_restoration_logical_report_requires_separate_link=$true;financial_diff_finalized=$false;baseline_adopted=$false;request_states_changed=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Depth 5 -Compress|Write-Output
  Write-Output 'SOURCE_BYTES_MATCH_BACKUP_NO_ORIGINAL_START_NO_LEDGER_CHANGE';return 0
 } catch {
  $reason='COMPARE_BLOCKED';if($_.Exception.Message -cmatch '^(COMPARE|RESTORE|COLD|BACKUP_PLAN)_[A-Z0-9_]{1,100}$'){$reason=$_.Exception.Message}
  [pscustomobject]@{source_compare='BLOCKED_KEEP_STOPPED_NO_RETRY';stage=$stage;failure_category=$reason;retained_helper_id=$helper;helper_stopped_verified=$stopped;originals_started=$false;source_data_written=$false;logical_account_row_queried=$false;financial_diff_finalized=$false;baseline_adopted=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output;return 2
 } finally {foreach($handle in $handles){$handle.Dispose()};if($locked){$mutex.ReleaseMutex()};if($null -ne $mutex){$mutex.Dispose()}}
}
