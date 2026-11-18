function Preview-True([object]$Value) {Inspect-Require ($Value -is [bool] -and $Value) 'PREVIEW_RECORDED_STATE_INVALID'}
function Preview-False([object]$Value) {Inspect-Require ($Value -is [bool] -and -not $Value) 'PREVIEW_RECORDED_STATE_INVALID'}
function Get-SavedCurrent([object]$Source,[object]$Plan,[string]$Parent) {
 Reference-AssertPrivateDirectory $Parent
 $folders=@(Get-ChildItem -LiteralPath $Parent -Directory -Filter 'current-read-20261118-*');Inspect-Require ($folders.Count -le 32) 'PREVIEW_SEARCH_LIMIT';$selected=@()
 $expected=@{
  'MODE_BEFORE.json'='/fapi/v1/accountConfig';'MODE_AFTER.json'='/fapi/v1/accountConfig'
  'BTC_CONFIG_BEFORE.json'='/fapi/v1/symbolConfig';'BTC_CONFIG_AFTER.json'='/fapi/v1/symbolConfig'
  'REGULAR_OPEN_1.json'='/fapi/v1/openOrders';'REGULAR_OPEN_2.json'='/fapi/v1/openOrders'
  'ALGO_OPEN_1.json'='/fapi/v1/openAlgoOrders';'ALGO_OPEN_2.json'='/fapi/v1/openAlgoOrders'
  'POSITIONS_1.json'='/fapi/v3/positionRisk';'POSITIONS_2.json'='/fapi/v3/positionRisk'
  'ACCOUNT_1.json'='/fapi/v3/account';'ACCOUNT_2.json'='/fapi/v3/account'
  'INCOME_GAP.json'='/fapi/v1/income';'BTC_TRADES_GAP.json'='/fapi/v1/userTrades'
 }
 foreach($folder in $folders){
  Inspect-Require ($folder.Name -cmatch '^current-read-20261118-[0-9a-f]{32}$') 'PREVIEW_DIRECTORY_INVALID';Reference-AssertPrivateDirectory $folder.FullName
  $path=Join-Path $folder.FullName 'RECEIPT.json';if(-not [IO.File]::Exists($path)){continue}
  $rf=Trades-Open $path 1048576 $Source.streams;$r=Inspect-Json $rf.body
  if($r.current_read -cne 'CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES'){continue}
  Inspect-Require ($null -eq $r.failure_category -and [string]::Equals($r.private_directory,$folder.FullName,[StringComparison]::OrdinalIgnoreCase) -and $r.day_key_kst -ceq $Source.day_key -and $r.week_key_kst -ceq $Source.week_key -and (Income-Decimal $r.saved_wallet_candidate_usdt) -eq (Income-Decimal $Source.wallet) -and $r.actual_http_attempts -eq 18 -and $r.export_jobs_created -eq 0 -and $r.order_writes -eq 0 -and $r.open_order_and_current_position_scope -ceq 'ALL_SYMBOLS' -and $r.btc_configuration_and_trade_history_scope -ceq 'BTCUSDT') 'PREVIEW_RECEIPT_SCOPE_INVALID'
  foreach($name in @('saved_source_hashes_verified_before_after','non_atomic_read_sequence')){Preview-True $r.$name}
  foreach($name in @('account_wide_historical_trade_history_verified','boundary_positions_independently_verified','equity_baseline_reconstructed','historic_requests_resolved','settings_changed_by_tool','old_export_guard_changed','automatic_retry','credentials_saved','source_files_written','request_states_changed','baseline_adopted','docker_used','services_started','execution_approved','orders_enabled','live_trading_enabled')){Preview-False $r.$name}
  Preview-True $r.summary.observations_consistent_with_expected_flat_profile
  Inspect-Require (@($r.summary.issues).Count -eq 0) 'PREVIEW_PROFILE_HAS_ISSUES'
  $intentFile=Trades-Open (Join-Path $folder.FullName 'INTENT.json') 16384 $Source.streams;$intent=Inspect-Json $intentFile.body
  Inspect-Require ($intent.policy -ceq 'DEMO_CURRENT_ACCOUNT_READ_20261118_V1' -and $intent.manifest_sha256 -ceq $Plan.manifest_sha256 -and $intent.saved_income_receipt_sha256 -ceq $Source.receipt_sha256 -and $intent.saved_trade_receipt_sha256 -ceq $Source.trade_receipt_sha256 -and $intent.export_jobs_allowed -eq 0) 'PREVIEW_INTENT_LINK_INVALID';Preview-False $intent.automatic_retry
  $endText=Income-Integer $r.observations_end_ms 'PREVIEW_WINDOW_INVALID';Inspect-Require ($endText -cmatch '^[0-9]{13}$') 'PREVIEW_WINDOW_INVALID';$end=[long]$endText
  $bounds=Income-Boundaries $end $Plan.week_start_kst
  Inspect-Require ($end -gt $Source.trade_end_ms -and $end-$Source.week_start_ms -le 604800000L -and $bounds.day_key -ceq $Source.day_key -and $bounds.week_key -ceq $Source.week_key) 'PREVIEW_WINDOW_INVALID'
  $names=@($r.raw_evidence|ForEach-Object {$_.file}|Sort-Object)
  Inspect-Require (($names -join '|') -ceq ((@($expected.Keys|Sort-Object)) -join '|')) 'PREVIEW_EVIDENCE_SCOPE_INVALID'
  $raw=@{}
  foreach($item in $r.raw_evidence){
   Inspect-Require ($item.sha256 -cmatch '^[0-9a-f]{64}$' -and $item.route -ceq $expected[$item.file]) 'PREVIEW_EVIDENCE_SCOPE_INVALID'
   $file=Trades-Open (Join-Path $folder.FullName $item.file) 4194304 $Source.streams
   Inspect-Require ($file.sha256 -ceq $item.sha256) 'PREVIEW_RAW_HASH_MISMATCH';$raw[$item.file]=$file.body
  }
  $modes=@();$configs=@();$accounts=@()
  foreach($suffix in @('BEFORE','AFTER')){
   $m=Current-Mode $raw[('MODE_'+$suffix+'.json')];Inspect-Require ($m.one_way -and $m.single_asset -and $m.account_can_trade) 'PREVIEW_MODE_INVALID';$modes+=,$m
   $c=Current-BtcConfig $raw[('BTC_CONFIG_'+$suffix+'.json')];Inspect-Require $c.expected_isolated_2x_no_auto_add 'PREVIEW_BTC_CONFIG_INVALID';$configs+=,$c
  }
  foreach($i in 1..2){
   $regular=Current-Orders $raw[('REGULAR_OPEN_'+$i+'.json')] 'REGULAR';$algo=Current-Orders $raw[('ALGO_OPEN_'+$i+'.json')] 'ALGO';$positions=Current-Positions $raw[('POSITIONS_'+$i+'.json')];$a=Current-Account $raw[('ACCOUNT_'+$i+'.json')]
   Inspect-Require ($regular.empty -and $algo.empty -and $positions.flat -and $positions.hedge_side_rows -eq 0 -and $a.flat_unencumbered_usdt -and $a.usdt_wallet -ceq $Source.wallet) 'PREVIEW_CURRENT_EXPOSURE_OR_WALLET_INVALID';$accounts+=,$a
  }
  Inspect-Require (($accounts[0]|ConvertTo-Json -Compress) -ceq ($accounts[1]|ConvertTo-Json -Compress)) 'PREVIEW_ACCOUNT_CHANGED'
  Inspect-Require ((Read-List $raw['INCOME_GAP.json']).returned_count -eq 0 -and (Read-List $raw['BTC_TRADES_GAP.json']).returned_count -eq 0) 'PREVIEW_ACTIVITY_GAP_NOT_EMPTY'
  $selected+=,[pscustomobject]@{receipt_sha256=$rf.sha256;observations_end_ms=$end;raw_file_count=$names.Count}
 }
 Inspect-Require ($selected.Count -eq 1) 'PREVIEW_CURRENT_NOT_UNIQUE'
 Trades-VerifySaved $Source
 return $selected[0]
}
function New-RecoveryPreview([object]$Source,[object]$Plan,[object]$Current,[object]$Baseline) {
 Inspect-Require ($Baseline.baseline_reference_inspection -ceq 'PINNED_PRIOR_BASELINES_AND_HISTORICAL_REPORT_READ') 'PREVIEW_BASELINE_UNAVAILABLE'
 $b=$Baseline.review
 Preview-False $b.current_database_state_verified;Preview-False $b.baseline_source_accepted_now
 Inspect-Require ($Plan.week_start_kst -eq 0 -and $Source.day_key -ceq '2026-11-18' -and $Source.week_key -ceq '2026-11-16') 'PREVIEW_PERIOD_INVALID'
 $states=@($Plan.anchors)+@($Plan.targets)
 Inspect-Require ($states.Count -eq 4 -and @($states|Where-Object {$_.role -ceq 'ENTRY' -and $_.request_state -ceq 'UNKNOWN'}).Count -eq 2 -and @($states|Where-Object {$_.role -ceq 'EXIT' -and $_.request_state -ceq 'SUBMITTING'}).Count -eq 1 -and @($states|Where-Object {$_.role -ceq 'STOP_LOSS' -and $_.request_state -ceq 'UNKNOWN'}).Count -eq 1) 'PREVIEW_REQUEST_SCOPE_INVALID'
 $anchor=[DateTimeOffset]::Parse($b.prior_snapshot_value_as_of_utc,[Globalization.CultureInfo]::InvariantCulture)
 $observed=[DateTimeOffset]::FromUnixTimeMilliseconds($Current.observations_end_ms)
 Inspect-Require (($observed-$anchor).TotalDays -gt 7) 'PREVIEW_LEGACY_ANCHOR_SCOPE_CHANGED'
 Trades-VerifySaved $Source
 return [pscustomobject]@{
  recovery_preview='OFFLINE_EVIDENCE_LINKED_CONDITIONAL_PROPOSAL_KEEP_STOPPED'
  source_scope='PINNED_BACKUP_OLD_ROW_AND_STORED_REPORT_NOT_CURRENT_DATABASE'
  evidence_hashes=[pscustomobject]@{backup_manifest_sha256=$Plan.manifest_sha256;income_receipt_sha256=$Source.receipt_sha256;trade_receipt_sha256=$Source.trade_receipt_sha256;current_receipt_sha256=$Current.receipt_sha256}
  saved_observations_end_utc=$observed.ToString('o');current_raw_files_verified=$Current.raw_file_count
  candidate=[pscustomobject]@{day_kst=$Source.day_key;week_kst=$Source.week_key;daily_base_usdt=$Source.wallet;weekly_base_usdt=$Source.wallet;source='SAVED_WALLET_CANDIDATE_NOT_VERIFIED_MIDNIGHT_EQUITY';applied=$false}
  prior_snapshot=[pscustomobject]@{day_kst=$b.prior_snapshot_day_kst;week_kst=$b.prior_snapshot_week_kst;daily_base_usdt=$b.recorded_prior_daily_base_usdt;weekly_base_usdt=$b.recorded_prior_weekly_base_usdt;valuation_as_of_utc=$b.prior_snapshot_value_as_of_utc}
  stored_day13_report_day_kst=$b.historical_report_day_kst
  preserve=[pscustomobject]@{high_water_usdt=$b.recorded_prior_high_water_usdt;value_components_usdt=$b.recorded_value_components_usdt;orders=34;unresolved_requests=4;requests=@($states|Select-Object role,request_state,request_reference_sha256);paused=$true}
  blockers=@('CURRENT_DATABASE_NOT_VERIFIED','LEGACY_APPLY_ANCHOR_EXCEEDS_7_DAYS','PERIOD_SOURCE_NOT_ACCEPTED','MIDNIGHT_EQUITY_NOT_INDEPENDENTLY_RECONSTRUCTED','HISTORICAL_REQUESTS_NOT_RESOLVED','NEW_APPLY_PATH_NOT_IMPLEMENTED_OR_APPROVED','STARTUP_AND_RISK_GATES_NOT_COMPLETED')
  source_hashes_verified_before_after=$true;local_receipt_hashes_are_not_independent_exchange_attestation=$true
  historical_account_wide_trade_history_verified=$false;initial_funding_verified=$false;baseline_source_accepted=$false
  current_database_state_verified=$false;apply_token_created=$false;baseline_adopted=$false;request_states_changed=$false
  files_written=$false;credentials_accessed=$false;actual_http_attempts=0;export_jobs_created=0;docker_used=$false;services_started=$false
  order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false
 }
}
