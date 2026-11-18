function Scope-SafeProbe([object]$Probe) {
 $safe=[ordered]@{}
 if($null -eq $Probe){return [pscustomobject]@{step='NOT_RECORDED'}}
 $steps=@('STATE_HEADER','PRESERVATION_HELPER','CONTAINER_COUNTS','ROLE_COUNT','STOPPED_STATE','PRIMARY_SELECTION','PRIMARY_BINDINGS','SAVED_SNAPSHOT','EXTERNAL_CONSUMERS','FINAL_SNAPSHOT','FRESH_CONTAINER_SET','FRESH_SNAPSHOT_MISMATCH','COMPLETE')
 if($steps -ccontains $Probe.step){$safe['step']=$Probe.step}else{$safe['step']='UNRECOGNIZED'}
 $numbers=@('labelled_project_count','project_count','saved_count','expected','actual','api_matches','database_matches','snapshot_index','container_matches','source_project_count','expected_count','actual_count')
 $flags=@('verified_helper_excluded','helper_had_project_label','database_volume_match','runtime_volume_match','api_image_reference_match','database_image_reference_match','api_image_resolves_to_canonical','database_image_resolves_to_canonical','image_match','service_match','restart_json_match','restart_values_match','mounts_json_match','mounts_values_match','restart_metadata_match','mounts_metadata_match')
 foreach($p in $Probe.PSObject.Properties){
  if($numbers -ccontains $p.Name -and ($p.Value -is [int] -or $p.Value -is [long] -or $p.Value -is [decimal]) -and $p.Value -ge -1 -and $p.Value -le 10000){$safe[$p.Name]=$p.Value}
  if($flags -ccontains $p.Name -and ($null -eq $p.Value -or $p.Value -is [bool])){$safe[$p.Name]=$p.Value}
  if($p.Name -ceq 'role' -and @('api','db','alerts','web','key-session','OTHER') -ccontains $p.Value){$safe['role']=$p.Value}
  if($p.Name -ceq 'fields'){$safe['fields']=@($p.Value|Where-Object {@('id','name','image','labels','mounts','running','restarting','paused','dead','restart','networks') -ccontains $_})}
 }
 return [pscustomobject]$safe
}
function Invoke-ScopeDiagnostic([string]$Root) {
 $script:RestoreScopeProbe=$null;$stage='INPUT_HASHES';$failure=$null
 try {
  $config=Read-RestoreJson (Join-Path $Root 'CONFIG.json')
  $backup=Find-RetainedBackup $config
  $receipt=Read-RestoreJson (Join-Path $backup 'BACKUP_RECEIPT.json')
  Assert-InputHashes $backup $config $receipt
  $state=Read-RestoreJson (Join-Path $backup 'BACKUP_STATE.json')
  $stage='ORIGINAL_SCOPE'
  $null=Read-RestoreSources $config $receipt $state
  $status='READ_ONLY_SCOPE_CHECK_PASSED_NO_COMPARISON'
 } catch {
  $status='READ_ONLY_SCOPE_BLOCK_LOCATION_RECORDED'
  $failure='SCOPE_DIAGNOSTIC_BLOCKED'
  if($_.Exception.Message -cmatch '^(RESTORE|COLD|BACKUP_PLAN)_[A-Z0-9_]{1,100}$'){$failure=$_.Exception.Message}
 }
 return [pscustomobject]@{source_scope_diagnostic=$status;stage=$stage;failure_category=$failure;scope_probe=(Scope-SafeProbe $script:RestoreScopeProbe);helper_created=$false;originals_started=$false;source_data_written=$false;source_bytes_compared=$false;logical_account_row_queried=$false;financial_diff_finalized=$false;baseline_adopted=$false;request_states_changed=$false;automatic_retry=$false;actual_http_attempts=0;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}
}
