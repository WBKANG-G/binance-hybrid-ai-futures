$script:FinancialState=$null
$script:FinancialConfig=$null
$script:FinancialPrior=$null
$script:FinancialBaseline=$null
$script:FinancialPlan=$null
function Container-Ids {
 $all=@(Financial-AllContainerIds)
 $id=$script:FinancialConfig.comparison_helper_id
 if($all -cnotcontains $id){throw 'FINANCIAL_REVIEWED_HELPER_MISSING'}
 # One exact user-observed helper ID is exempted only after full read-only admission.
 Assert-CompareHelper $id $script:FinancialState @([pscustomobject]@{id=$script:FinancialConfig.api_container_id},[pscustomobject]@{id=$script:FinancialConfig.database_container_id})
 $exit=@(Backup-Docker @('inspect','--format','{{.State.ExitCode}} {{.State.OOMKilled}}',$id) 1)
 if($exit.Count -ne 1 -or $exit[0] -cne '0 false'){throw 'FINANCIAL_REVIEWED_HELPER_EXIT_CHANGED'}
 return @($all|Where-Object {$_ -cne $id})
}
function Read-CopySources([object]$Config,[object]$Receipt,[object]$State) {
 $script:FinancialState=$State;$script:FinancialConfig=$Config
 $path=Find-RestoreEvidence $Config
 $handle=New-Object IO.FileStream($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 try {
  if((Restore-Hash $path) -cne $Config.restore_receipt_sha256){throw 'HANDOFF_RESTORE_HASH_MISMATCH'}
  $recovery=Read-RestoreJson $path;Assert-RestoreEvidence $recovery $Config
  $items=Assert-HandoffStoppedScope $Config $Receipt $State $recovery
  $script:FinancialPrior=$recovery.account
  $backup=Find-RetainedBackup $Config
  $bind=@(@($items|Where-Object {$_.id -ceq $recovery.retained_helper_ids[0]})[0].mounts|Where-Object {$_.Destination -ceq '/bundle'})
  if($bind.Count -ne 1 -or -not [string]::Equals($bind[0].Source.Replace('/','\').TrimEnd('\'),$backup.Replace('/','\').TrimEnd('\'),[StringComparison]::OrdinalIgnoreCase)){throw 'HANDOFF_RESTORE_BIND_SOURCE_CHANGED'}
  $parent=Split-Path -Parent $backup
  $baseline=Invoke-BaselineReferenceInspection $parent $Config.manifest_sha256
  if($baseline.baseline_reference_inspection -cne 'PINNED_PRIOR_BASELINES_AND_HISTORICAL_REPORT_READ'){throw 'FINANCIAL_BASELINE_UNAVAILABLE'}
  $script:FinancialBaseline=$baseline.review
  $script:FinancialPlan=Invoke-IncomePlan $parent $Config.manifest_sha256
  $script:CopyRestoreHandle=$handle
  return ,$items
 } catch {$handle.Dispose();throw}
}
function Assert-CopyFinancial([object]$Account) {
 $expected=@('account_state_sha256','daily_key','orders','paused','transaction_read_only','unresolved_requests','weekly_key','financial','request_states','risk_state','reconciled','stream_ready','last_reconciled')
 if((@($Account.PSObject.Properties.Name|Sort-Object) -join '|') -cne (@($expected|Sort-Object) -join '|')){throw 'FINANCIAL_SQL_SCHEMA_INVALID'}
 if($Account.account_state_sha256 -cne $script:FinancialPrior.account_state_sha256 -or $Account.paused -isnot [bool] -or -not $Account.paused -or $Account.transaction_read_only -cne 'on' -or $Account.orders -ne 34 -or $Account.unresolved_requests -ne 4 -or $Account.daily_key -cne '2026-11-13' -or $Account.weekly_key -cne '2026-11-09' -or $Account.risk_state -cne 'NORMAL' -or $Account.reconciled -isnot [bool] -or $Account.stream_ready -isnot [bool]){throw 'FINANCIAL_SQL_STATE_MISMATCH'}
 $f=$Account.financial;$b=$script:FinancialBaseline
 if((@($f.PSObject.Properties.Name|Sort-Object) -join '|') -cne 'available_balance|daily_base|fees|funding|high_water|initial|net_deposits|realized|unrealized|value_as_of|weekly_base'){throw 'FINANCIAL_SQL_SCHEMA_INVALID'}
 foreach($name in @('initial','realized','unrealized','fees','funding','net_deposits')){if((Baseline-Money $f.$name) -ne (Baseline-Money $b.recorded_value_components_usdt.$name)){throw 'FINANCIAL_VALUE_COMPONENT_MISMATCH'}}
 foreach($name in @('daily_base','weekly_base','high_water','available_balance')){$property='recorded_prior_'+$name+'_usdt';if((Baseline-Money $f.$name) -ne (Baseline-Money $b.$property)){throw 'FINANCIAL_PERIOD_OR_HIGH_WATER_MISMATCH'}}
 if([DateTimeOffset]::Parse((Baseline-Time $f.value_as_of)) -ne [DateTimeOffset]::Parse($b.prior_snapshot_value_as_of_utc)){throw 'FINANCIAL_VALUATION_TIME_CHANGED'}
 $requests=@($Account.request_states);$plan=@($script:FinancialPlan.anchors)+@($script:FinancialPlan.targets)
 if($requests.Count -ne 4){throw 'FINANCIAL_REQUEST_SCOPE_CHANGED'}
 $seen=@{}
 foreach($r in $requests){$pin=$r.request_reference_sha256;if($pin -cnotmatch '^[0-9a-f]{64}$' -or $seen.ContainsKey($pin)){throw 'FINANCIAL_REQUEST_SCOPE_CHANGED'};$seen[$pin]=$true;$matches=@($plan|Where-Object {$_.request_reference_sha256 -ceq $pin -and $_.request_state -ceq $r.request_state});if($matches.Count -ne 1){throw 'FINANCIAL_REQUEST_SCOPE_CHANGED'}}
}
