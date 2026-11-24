$script:InspectionVolume=$null
$script:InspectionApproved=$false
$script:InspectionId=$null
$script:InspectionArgs=$null
$script:InspectionStarted=$false
$script:InspectionEverRunning=$false
$script:InspectionStopped=$false
function Inspection-Require([bool]$Condition,[string]$Code){if(-not $Condition){throw $Code}}
function Assert-InspectionApproval([string]$Root,[object]$Config) {
 Inspection-Require ($Config.inspection_approval_receipt_file_sha256 -is [string] -and $Config.inspection_approval_receipt_file_sha256 -cmatch '^[0-9a-f]{64}$') 'ORIGINAL_START_APPROVAL_REQUIRED'
 $path=Join-Path $Root 'ORIGINAL_START_ACCEPTANCE.json'
 Inspection-Require ((Restore-Hash $path) -ceq $Config.inspection_approval_receipt_file_sha256) 'ORIGINAL_APPROVAL_HASH_MISMATCH'
 Inspection-Require ((Restore-Hash (Join-Path $Root 'INSPECTION_PROPOSAL.json')) -ceq $Config.inspection_proposal_file_sha256) 'ORIGINAL_PROPOSAL_HASH_MISMATCH'
 $proposal=Read-RestoreJson (Join-Path $Root 'INSPECTION_PROPOSAL.json')
 Inspection-Require ($proposal.policy -ceq 'ORIGINAL_DATABASE_INSPECTION_ONLY_PROPOSAL_V1' -and $proposal.database_volume -ceq 'hybrid-windows_postgres_data' -and $proposal.runtime_volume_mounted_in_temporary_server -is [bool] -and -not $proposal.runtime_volume_mounted_in_temporary_server) 'ORIGINAL_PROPOSAL_SCOPE_INVALID'
 Inspection-Require ($Config.manifest_sha256 -ceq $proposal.source_manifest_sha256 -and $Config.receipt_sha256 -ceq $proposal.backup_receipt_sha256 -and $Config.comparison_helper_id -ceq $proposal.prestart_comparison_helper_id -and $Config.database_container_id -ceq $proposal.original_database_container_id -and $Config.api_container_id -ceq $proposal.original_api_container_id) 'ORIGINAL_CONFIG_SCOPE_MISMATCH'
 $r=Read-RestoreJson $path
 Inspection-Require ($r.policy -ceq 'ORIGINAL_DATABASE_INSPECTION_ONLY_ACCEPTANCE_V1' -and $r.proposal_file_sha256 -ceq $Config.inspection_proposal_file_sha256 -and $r.original_database_start_authorized -is [bool] -and $r.original_database_start_authorized -and $r.postgresql_startup_shutdown_storage_writes_authorized -is [bool] -and $r.postgresql_startup_shutdown_storage_writes_authorized -and $r.single_attempt_only -is [bool] -and $r.single_attempt_only) 'ORIGINAL_APPROVAL_SCOPE_INVALID'
 foreach($k in @('original_ledger_write_authorized','owner_lease_takeover_authorized','baseline_adoption_authorized','request_resolution_authorized','resend_authorized','execution_approved','orders_enabled','live_trading_enabled')){Inspection-Require ($r.$k -is [bool] -and -not $r.$k) 'ORIGINAL_APPROVAL_AUTHORITY_INVALID'}
 Inspection-Require ($r.approval_id -cmatch '^[0-9a-f]{64}$' -and $r.operator_reply -is [string] -and $r.operator_reply.Length -gt 0) 'ORIGINAL_APPROVAL_RECORD_INVALID'
 $script:InspectionVolume=$proposal.database_volume
 $script:InspectionApproved=$true
 return $r.approval_id
}
function Inspection-Process([string[]]$Arguments,[string]$InputText='',[int]$Timeout=15000) {
Inspection-Require ($InputText.Length -le 65536 -and $InputText -cnotmatch '[^\x00-\x7f]') 'ORIGINAL_SQL_INPUT_INVALID'
 $commands=@(Get-Command docker -CommandType Application -ErrorAction SilentlyContinue)
 Inspection-Require ($commands.Count -ge 1) 'ORIGINAL_DOCKER_CLI_UNAVAILABLE'
 $info=New-Object Diagnostics.ProcessStartInfo;$info.FileName=$commands[0].Source
 $info.Arguments=(@($Arguments|ForEach-Object {Scope-NativeArgument $_}) -join ' ')
 $info.UseShellExecute=$false;$info.CreateNoWindow=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true;$info.RedirectStandardInput=$true
 $info.StandardOutputEncoding=New-Object Text.UTF8Encoding($false);$info.StandardErrorEncoding=New-Object Text.UTF8Encoding($false)
 $p=New-Object Diagnostics.Process;$p.StartInfo=$info;$started=$false
 try {
  $started=$p.Start();Inspection-Require $started 'ORIGINAL_DOCKER_CLIENT_START_FAILED'
  $out=$p.StandardOutput.ReadToEndAsync();$err=$p.StandardError.ReadToEndAsync()
  $task=$p.StandardInput.WriteAsync($InputText);Inspection-Require ($task.Wait(3000)) 'ORIGINAL_DOCKER_INPUT_TIMEOUT';$p.StandardInput.Close()
  Inspection-Require ($p.WaitForExit($Timeout)) 'ORIGINAL_DOCKER_CLIENT_TIMEOUT'
  Inspection-Require ($out.Wait(2000) -and $err.Wait(2000)) 'ORIGINAL_DOCKER_OUTPUT_TIMEOUT'
  Inspection-Require ($out.Result.Length -le 65536 -and $err.Result.Length -le 1048576) 'ORIGINAL_DOCKER_OUTPUT_LIMIT'
  return [pscustomobject]@{exit_code=$p.ExitCode;stdout=$out.Result;stderr_present=($err.Result.Length -gt 0)}
 } finally {try{if($started -and -not $p.HasExited){$p.Kill()}}catch{};$p.Dispose()}
}
function Inspection-ServerArgs([object]$State,[object]$Manifest) {
 Inspection-Require $script:InspectionApproved 'ORIGINAL_START_APPROVAL_REQUIRED'
 Inspection-Require ($State.database_volume -ceq $script:InspectionVolume -and $State.canonical_images[1] -cmatch '^sha256:[0-9a-f]{64}$') 'ORIGINAL_VOLUME_OR_IMAGE_SCOPE_INVALID'
 Inspection-Require (($null -eq $env:LIVE_TRADING_ENABLED -or $env:LIVE_TRADING_ENABLED -ieq 'false') -and $env:TRADING_MODE -ine 'LIVE') 'ORIGINAL_LIVE_CONTEXT_FORBIDDEN'
 $pg=@($Manifest.groups.database.entries|Where-Object {$_.path -ceq '18/docker' -and $_.kind -ceq 'directory'})
 Inspection-Require ($pg.Count -eq 1 -and ($pg[0].uid -is [int] -or $pg[0].uid -is [long]) -and ($pg[0].gid -is [int] -or $pg[0].gid -is [long]) -and $pg[0].uid -ge 1 -and $pg[0].uid -le 65535 -and $pg[0].gid -ge 0 -and $pg[0].gid -le 65535) 'ORIGINAL_PGDATA_METADATA_INVALID'
 $args=@('create','--pull=never','--label','com.docker.compose.project=demo-original-inspection','--network','none','--restart','no','--read-only','--memory','1g','--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777','--user',([string]$pg[0].uid+':'+[string]$pg[0].gid),'--mount',('type=volume,source='+$State.database_volume+',target=/var/lib/postgresql,volume-nocopy'),'--entrypoint','postgres',$State.canonical_images[1],'-D','/var/lib/postgresql/18/docker')
 foreach($setting in @('listen_addresses=','unix_socket_directories=/tmp','default_transaction_read_only=on','autovacuum=off','ssl=off','archive_mode=off','shared_preload_libraries=','session_preload_libraries=','local_preload_libraries=','max_worker_processes=0','primary_conninfo=','primary_slot_name=','restore_command=','archive_command=','archive_cleanup_command=','recovery_end_command=','logging_collector=off','log_destination=stderr')){$args+=@('-c',$setting)}
 return $args
}
function Assert-InspectionServer([object]$State,[object[]]$Original) {
 $id=$script:InspectionId
 Inspection-Require ($id -is [string] -and $id -cmatch '^[0-9a-f]{64}$' -and @($Original.id) -cnotcontains $id) 'ORIGINAL_SERVER_ID_INVALID'
 $v=Read-Container $id
 Inspection-Require ($v.image -ceq $State.canonical_images[1] -and $v.restart.Name -ceq 'no' -and $v.restart.MaximumRetryCount -eq 0 -and -not $v.restarting -and -not $v.paused -and -not $v.dead) 'ORIGINAL_SERVER_STATE_INVALID'
 $mounts=@($v.mounts)
 Inspection-Require ($mounts.Count -eq 1 -and $mounts[0].Type -ceq 'volume' -and $mounts[0].Name -ceq $State.database_volume -and $mounts[0].Destination -ceq '/var/lib/postgresql' -and $mounts[0].RW -is [bool] -and $mounts[0].RW) 'ORIGINAL_SERVER_MOUNT_INVALID'
 $rows=@(Backup-Docker @('inspect','--format','{{json .HostConfig}}{{println}}{{json .Config.User}}{{println}}{{json .Config.Entrypoint}}{{println}}{{json .Config.Cmd}}',$id) 4)
 Inspection-Require ($rows.Count -eq 4) 'ORIGINAL_SERVER_CONFIGURATION_INVALID'
 $hostConfig=ConvertFrom-Json -InputObject $rows[0];$user=ConvertFrom-Json -InputObject ('{"v":'+$rows[1]+'}');$entry=ConvertFrom-Json -InputObject ('{"v":'+$rows[2]+'}');$command=ConvertFrom-Json -InputObject ('{"v":'+$rows[3]+'}')
 $i=[Array]::IndexOf($script:InspectionArgs,'--user');$e=[Array]::IndexOf($script:InspectionArgs,'-D')
 Inspection-Require ($i -ge 0 -and $e -ge 0 -and $user.v -ceq $script:InspectionArgs[$i+1] -and (@($entry.v)-join '|') -ceq 'postgres' -and (@($command.v)-join '|') -ceq (@($script:InspectionArgs[$e..($script:InspectionArgs.Count-1)])-join '|')) 'ORIGINAL_SERVER_COMMAND_INVALID'
 Inspection-Require ($hostConfig.NetworkMode -ceq 'none' -and $hostConfig.ReadonlyRootfs -and -not $hostConfig.Privileged -and $hostConfig.Memory -eq 1073741824 -and $hostConfig.PidsLimit -eq 128 -and (@($hostConfig.CapDrop)-join '|') -ceq 'ALL' -and ($null -eq $hostConfig.CapAdd -or @($hostConfig.CapAdd).Count -eq 0) -and (@($hostConfig.SecurityOpt)-join '|') -ceq 'no-new-privileges' -and ($null -eq $hostConfig.Devices -or @($hostConfig.Devices).Count -eq 0) -and ($null -eq $hostConfig.Binds -or @($hostConfig.Binds).Count -eq 0) -and ($null -eq $hostConfig.PortBindings -or @($hostConfig.PortBindings.PSObject.Properties).Count -eq 0) -and -not $hostConfig.PublishAllPorts -and $hostConfig.PidMode -ceq '' -and $hostConfig.IpcMode -ceq 'private' -and $hostConfig.UTSMode -ceq '') 'ORIGINAL_SERVER_ISOLATION_INVALID'
 Inspection-Require ((@($hostConfig.Tmpfs.PSObject.Properties.Name)-join '|') -ceq '/tmp' -and $hostConfig.Tmpfs.'/tmp' -ceq 'rw,nosuid,nodev,noexec,size=64m,mode=1777') 'ORIGINAL_SERVER_TMPFS_INVALID'
 return $v
}
function Stop-InspectionServer([object]$State,[object[]]$Original) {
 $v=Assert-InspectionServer $State $Original
 if($v.running){
  $reply=Inspection-Process @('kill','--signal=SIGINT',$script:InspectionId)
  Inspection-Require ($reply.exit_code -eq 0) 'ORIGINAL_SERVER_SHUTDOWN_SIGNAL_FAILED'
  $w=[Diagnostics.Stopwatch]::StartNew()
  do{$v=Assert-InspectionServer $State $Original;if(-not $v.running){break};Start-Sleep -Milliseconds 250}while($w.ElapsedMilliseconds -lt 30000)
 }
 Inspection-Require (-not $v.running) 'ORIGINAL_SERVER_STOP_NOT_VERIFIED'
 $exit=@(Backup-Docker @('inspect','--format','{{.State.ExitCode}} {{.State.OOMKilled}}',$script:InspectionId) 1)
 Inspection-Require ($exit.Count -eq 1 -and $exit[0] -ceq '0 false') 'ORIGINAL_SERVER_CLEAN_EXIT_NOT_VERIFIED'
 $script:InspectionStopped=$true
}
function Invoke-OriginalInspection([string]$Root,[object]$Config,[object]$State,[object[]]$Original,[string]$Directory,[object]$Manifest) {
 Inspection-Require $script:InspectionApproved 'ORIGINAL_START_APPROVAL_REQUIRED'
 $script:InspectionArgs=@(Inspection-ServerArgs $State $Manifest)
 try {
  Assert-RestoreSourceSnapshot $Original @($Config.comparison_helper_id) $false
  $reply=Inspection-Process $script:InspectionArgs
  Inspection-Require ($reply.exit_code -eq 0 -and $reply.stdout.Trim() -cmatch '^[0-9a-f]{64}$') 'ORIGINAL_SERVER_CREATE_NOT_VERIFIED'
  $script:InspectionId=$reply.stdout.Trim()
  Write-BackupJson (Join-Path $Directory 'ORIGINAL_SERVER_RESOURCE.json') @{container_id=$script:InspectionId;original_database_volume=$State.database_volume;original_runtime_mounted=$false;network='none';logical_writes_authorized=$false;automatic_retry=$false}
  $v=Assert-InspectionServer $State $Original;Inspection-Require (-not $v.running) 'ORIGINAL_SERVER_ALREADY_RUNNING'
  Assert-RestoreSourceSnapshot $Original @($Config.comparison_helper_id,$script:InspectionId) $false
  $script:InspectionStarted=$true
  $reply=Inspection-Process @('start',$script:InspectionId)
  Inspection-Require ($reply.exit_code -eq 0) 'ORIGINAL_SERVER_START_NOT_VERIFIED'
  $watch=[Diagnostics.Stopwatch]::StartNew();$ready=$false
  do{
   $v=Assert-InspectionServer $State $Original;Inspection-Require $v.running 'ORIGINAL_SERVER_EXITED_DURING_START'
   $script:InspectionEverRunning=$true
   $reply=Inspection-Process @('exec',$script:InspectionId,'pg_isready','-h','/tmp','-U','dev','-d','hybrid','-t','1') '' 5000
   if($reply.exit_code -eq 0){$ready=$true;break};Start-Sleep -Milliseconds 250
  }while($watch.ElapsedMilliseconds -lt 60000)
  Inspection-Require $ready 'ORIGINAL_SERVER_READY_TIMEOUT'
  $sql=[IO.File]::ReadAllText((Join-Path $Root 'original_read.sql'))
  $seen=@();$account=$null
  foreach($n in 1..2){
   $null=Assert-InspectionServer $State $Original
   $reply=Inspection-Process @('exec','-i','--env','PGCONNECT_TIMEOUT=3',$script:InspectionId,'psql','-X','-q','-t','-A','--no-password','-v','ON_ERROR_STOP=1','-h','/tmp','-U','dev','-d','hybrid') $sql 15000
   Inspection-Require ($reply.exit_code -eq 0 -and $reply.stdout.Length -le 8192) 'ORIGINAL_SQL_READ_FAILED'
   $lines=@($reply.stdout.Trim() -split '\r?\n');Inspection-Require ($lines.Count -eq 1) 'ORIGINAL_SQL_OUTPUT_INVALID'
   $account=ConvertFrom-Json -InputObject $lines[0];Assert-OriginalRead $account
   $seen+=,($account|ConvertTo-Json -Depth 8 -Compress)
  }
  Inspection-Require ($seen[0] -ceq $seen[1]) 'ORIGINAL_ACCOUNT_CHANGED_BETWEEN_READS'
  Stop-InspectionServer $State $Original
  Assert-RestoreSourceSnapshot $Original @($Config.comparison_helper_id,$script:InspectionId) $false
  $result=[ordered]@{original_inspection='ORIGINAL_SQL_READ_ONLY_OBSERVED_AND_TEMP_SERVER_CLEANLY_STOPPED';current_original_sql_queried=$true;current_original_fence_verified=$true;current_original_owner_observed=$true;original_row_lock_acquired=$false;ownership=$account.owner_review;account_state_pg_text_sha256=$account.account_state_sha256;financial=$account.financial;request_states=$account.request_states;period_candidate=$account.period_candidate;original_database_start_attempted=$true;original_database_ever_running=$script:InspectionEverRunning;original_database_files_may_have_changed=$true;original_database_storage_changes_independently_enumerated=$false;original_ledger_write_attempted=$false;original_service_containers_started=$false;temporary_original_server_id=$script:InspectionId;temporary_original_server_stopped=$true;retained_container_not_deleted=$true;original_owner_lease_changed_by_tool=$false;original_fence_changed_by_tool=$false;original_row_and_advisory_locks_acquired=$false;fresh_exchange_evidence_checked=$false;baseline_adopted=$false;request_states_changed=$false;original_apply_ready=$false;apply_token_created=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
  Write-BackupJson (Join-Path $Directory 'ORIGINAL_INSPECTION_RECEIPT.json') $result
  $result|ConvertTo-Json -Depth 8 -Compress|Write-Output
  Write-Output 'ORIGINAL_INSPECTION_COMPLETE_TEMP_SERVER_STOPPED_NO_LEDGER_WRITE_NO_ORDERS'
 }finally{
  if($null -ne $script:InspectionId -and -not $script:InspectionStopped){try{Stop-InspectionServer $State $Original}catch{}}
 }
}
