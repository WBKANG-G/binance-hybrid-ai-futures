function Conditional-Zero([object]$Value) {return (($Value -is [int] -or $Value -is [long]) -and $Value -eq 0)}
function Conditional-Flag([object]$Value,[bool]$Expected) {return ($Value -is [bool] -and $Value -eq $Expected)}
function Assert-ConditionalObservation([object]$R,[object]$Source,[long]$Now) {
 $p=$script:AcceptedSource.policy
 Inspect-Require ($p.scope -ceq 'SOURCE_REVIEW_ONLY_WITHIN_SAME_WEEK' -and (Conditional-Flag $p.activated $false) -and $p.first_day_kst -ceq '2026-11-24' -and $p.last_day_kst -ceq '2026-11-29' -and $p.week_kst -ceq '2026-11-23' -and $p.fixed_usdt -ceq '4998.74183608' -and $p.max_observation_age_ms -eq 300000 -and $p.max_verified_reference_gap_ms -eq 604800000) 'CURRENT_REUSE_POLICY_INVALID'
 foreach($k in @('requires_new_verified_observation_for_each_day','requires_two_flat_stable_account_rounds','requires_no_income_or_btc_trade_gap_rows','requires_unsaturated_gap_pages','requires_verified_source_hashes','does_not_waive_existing_apply_guards')){Inspect-Require (Conditional-Flag $p.$k $true) 'CURRENT_REUSE_POLICY_INVALID'}
 $end=$R.observations_end_ms;$start=$R.observation_started_server_ms
 Inspect-Require (($end -is [long] -or $end -is [int]) -and ($start -is [long] -or $start -is [int]) -and $Now -ge $end -and $Now-$end -le 300000L -and $Now -ge $start -and $Now-$start -le 300000L) 'CURRENT_REUSE_CLOCK_INVALID'
 foreach($ref in @($Source.old_cutoff_ms,$Source.trade_end_ms)){Inspect-Require ($end -ge $ref -and $end-$ref -le 604800000L -and $Now-$ref -le 604800000L) 'CURRENT_REUSE_REFERENCE_GAP_INVALID'}
 $at=Income-Boundaries $end 0;$nowKeys=Income-Boundaries $Now 0
 Inspect-Require ($at.day_key -cge $p.first_day_kst -and $at.day_key -cle $p.last_day_kst -and $at.week_key -ceq $p.week_kst -and $nowKeys.day_key -ceq $at.day_key -and $R.observed_day_key_kst -ceq $at.day_key -and $R.observed_week_key_kst -ceq $at.week_key) 'CURRENT_REUSE_PERIOD_INVALID'
 Inspect-Require ($R.current_read -ceq 'CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES' -and (Conditional-Flag $R.saved_source_hashes_verified_before_after $true) -and $R.open_order_and_current_position_scope -ceq 'ALL_SYMBOLS' -and $R.btc_configuration_and_trade_history_scope -ceq 'BTCUSDT') 'CURRENT_REUSE_EVIDENCE_INVALID'
 foreach($k in @('settings_changed_by_tool','request_states_changed','baseline_adopted','execution_approved','orders_enabled','live_trading_enabled','account_wide_historical_trade_history_verified','boundary_positions_independently_verified','equity_baseline_reconstructed')){Inspect-Require (Conditional-Flag $R.$k $false) 'CURRENT_REUSE_CLAIM_INVALID'}
 Inspect-Require (Conditional-Zero $R.order_writes) 'CURRENT_REUSE_CLAIM_INVALID'
 $s=$R.summary
 Inspect-Require (@($s.issues).Count -eq 0 -and (Conditional-Flag $s.observations_consistent_with_expected_flat_profile $true)) 'CURRENT_REUSE_PROFILE_INVALID'
 foreach($k in @('mode_equal_at_reads','btc_config_equal_at_reads','account_equal_at_reads')){Inspect-Require (Conditional-Flag $s.$k $true) 'CURRENT_REUSE_PROFILE_INVALID'}
 foreach($suffix in @('before','after')){
  $m=$s.('mode_'+$suffix);$c=$s.('btc_config_'+$suffix)
  foreach($k in @('one_way','single_asset','account_can_trade')){Inspect-Require (Conditional-Flag $m.$k $true) 'CURRENT_REUSE_PROFILE_INVALID'}
  Inspect-Require ($c.symbol -ceq 'BTCUSDT' -and ($c.leverage -is [int] -or $c.leverage -is [long]) -and $c.leverage -eq 2 -and $c.margin_type -ceq 'ISOLATED' -and (Conditional-Flag $c.automatic_margin_addition $false)) 'CURRENT_REUSE_PROFILE_INVALID'
 }
 Inspect-Require (($s.mode_before|ConvertTo-Json -Compress) -ceq ($s.mode_after|ConvertTo-Json -Compress) -and ($s.btc_config_before|ConvertTo-Json -Compress) -ceq ($s.btc_config_after|ConvertTo-Json -Compress) -and @($s.rounds).Count -eq 2) 'CURRENT_REUSE_PROFILE_INVALID'
 foreach($row in $s.rounds){
  foreach($kind in @('regular_orders','algo_orders')){Inspect-Require ((Conditional-Zero $row.$kind.count) -and (Conditional-Flag $row.$kind.empty $true)) 'CURRENT_REUSE_PROFILE_INVALID'}
  $pos=$row.positions;$a=$row.account
  Inspect-Require (Conditional-Flag $pos.flat $true) 'CURRENT_REUSE_PROFILE_INVALID'
  foreach($k in @('nonzero_positions','nonzero_non_btc_positions','hedge_side_rows')){Inspect-Require (Conditional-Zero $pos.$k) 'CURRENT_REUSE_PROFILE_INVALID'}
  foreach($k in @('usdt_wallet','usdt_available','usdt_margin_balance')){Inspect-Require ($a.$k -is [string] -and $a.$k -ceq $p.fixed_usdt) 'CURRENT_REUSE_AMOUNT_INVALID'}
  foreach($k in @('positions_flat','flat_unencumbered_usdt')){Inspect-Require (Conditional-Flag $a.$k $true) 'CURRENT_REUSE_PROFILE_INVALID'}
  foreach($k in @('total_initial_margin','total_unrealized_profit')){Inspect-Require ($a.$k -is [string] -and (Income-Decimal $a.$k) -eq 0) 'CURRENT_REUSE_PROFILE_INVALID'}
 }
 Inspect-Require (($s.rounds[0].account|ConvertTo-Json -Compress) -ceq ($s.rounds[1].account|ConvertTo-Json -Compress)) 'CURRENT_REUSE_PROFILE_INVALID'
 foreach($k in @('income_gap_rows','btc_trade_gap_rows')){Inspect-Require (Conditional-Zero $s.$k) 'CURRENT_REUSE_ACTIVITY_INVALID'}
 foreach($k in @('income_gap_page_limit_reached','trade_gap_page_limit_reached')){Inspect-Require (Conditional-Flag $s.$k $false) 'CURRENT_REUSE_ACTIVITY_INVALID'}
 return $at
}
function Assert-ConditionalRaw([object]$Result) {
 $expected=@('MODE_BEFORE','BTC_CONFIG_BEFORE','REGULAR_OPEN_1','ALGO_OPEN_1','POSITIONS_1','ACCOUNT_1','REGULAR_OPEN_2','ALGO_OPEN_2','POSITIONS_2','ACCOUNT_2','MODE_AFTER','BTC_CONFIG_AFTER','INCOME_GAP','BTC_TRADES_GAP')|ForEach-Object {$_+'.json'}
 Inspect-Require ((@($Result.raw_evidence.file|Sort-Object)-join '|') -ceq (@($expected|Sort-Object)-join '|')) 'CURRENT_REUSE_RAW_SCOPE_INVALID'
 foreach($item in $Result.raw_evidence){
  Inspect-Require ($item.sha256 -cmatch '^[0-9a-f]{64}$') 'CURRENT_REUSE_RAW_HASH_INVALID'
  $stream=Inspect-Open (Join-Path $Result.private_directory $item.file) 4194304
  try{Inspect-Require ((Inspect-StreamHash $stream) -ceq $item.sha256) 'CURRENT_REUSE_RAW_HASH_MISMATCH'}finally{$stream.Dispose()}
 }
}
function Add-ConditionalBinding([object]$Result,[object]$Source,[object]$Now) {
 $bound=$false;$reason=$null;$candidate=$null
 try {
  Inspect-Require (($Now -is [int] -or $Now -is [long]) -and $Result.current_read -ceq 'CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES') 'CURRENT_REUSE_COLLECTION_INCOMPLETE'
  $candidate=Assert-ConditionalObservation $Result $Source $Now
  Trades-VerifySaved $Source;Assert-ConditionalRaw $Result
  $bound=$true
 }catch{$reason='CURRENT_REUSE_BINDING_BLOCKED';if($_.Exception.Message -cmatch '^(CURRENT|TRADES|INCOME|INSPECT)_[A-Z0-9_]{1,100}$'){$reason=$_.Exception.Message}}
 $fields=@{conditional_source_bound=$bound;conditional_reuse_evaluated=$true;conditional_binding_failure_category=$reason;source_acceptance_recorded=$true;conditional_reuse_approved=$true;source_accepted=$bound;fresh_raw_evidence_hashes_rechecked=$bound;server_now_ms=$Now;candidate_day_kst=$(if($bound){$candidate.day_key}else{$null});candidate_week_kst=$(if($bound){$candidate.week_key}else{$null});candidate_usdt=$(if($bound){'4998.74183608'}else{$null});apply_token_created=$false;original_apply_ready=$false;original_database_start_authorized=$false;original_database_write_authorized=$false;original_owner_lease_takeover_authorized=$false;request_resolution_authorized=$false;resend_authorized=$false;source_refresh_context='PRIOR_USER_SCREENSHOT_REPORTED_TWO_PASS_MATCH_HELPER_STOPPED';original_source_bytes_rechecked_by_this_tool=$false;current_original_sql_queried=$false;current_original_fence_verified=$false}
 foreach($k in $fields.Keys){$Result|Add-Member -NotePropertyName $k -NotePropertyValue $fields[$k] -Force}
}
