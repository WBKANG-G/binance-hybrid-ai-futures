function Current-Bool([object]$Value) {Inspect-Require ($Value -is [bool]) 'CURRENT_BOOLEAN_SCHEMA_INVALID';return $Value}
function Current-Mode([string]$Body) {
 $data=Export-Decode $Body;Inspect-Require ($data.GetType() -eq [System.Management.Automation.PSCustomObject]) 'CURRENT_MODE_SCHEMA_INVALID'
 return [pscustomobject]@{one_way=(-not (Current-Bool $data.dualSidePosition));single_asset=(-not (Current-Bool $data.multiAssetsMargin));account_can_trade=(Current-Bool $data.canTrade)}
}
function Current-BtcConfig([string]$Body) {
 $list=Read-List $Body;$rows=@($list.rows|Where-Object {$_.symbol -ceq 'BTCUSDT'});Inspect-Require ($rows.Count -eq 1) 'CURRENT_BTC_CONFIG_NOT_UNIQUE'
 $row=$rows[0];$leverage=Income-Integer $row.leverage 'CURRENT_LEVERAGE_INVALID';Inspect-Require ($leverage -cmatch '^[1-9][0-9]{0,2}$' -and @('ISOLATED','CROSSED') -ccontains $row.marginType) 'CURRENT_BTC_CONFIG_SCHEMA_INVALID'
 $auto=$row.isAutoAddMargin;Inspect-Require ($auto -is [bool] -or ($auto -is [string] -and @('true','false') -ccontains $auto)) 'CURRENT_AUTO_MARGIN_SCHEMA_INVALID'
 $autoOn=$auto -eq $true -or $auto -ceq 'true'
 return [pscustomobject]@{symbol='BTCUSDT';leverage=[int]$leverage;margin_type=$row.marginType;automatic_margin_addition=$autoOn;expected_isolated_2x_no_auto_add=($leverage -ceq '2' -and $row.marginType -ceq 'ISOLATED' -and -not $autoOn)}
}
function Current-Orders([string]$Body,[string]$Role) {
 $list=Read-List $Body;$ids=@{};$other=0
 foreach($row in $list.rows){
  Inspect-Require ($row.symbol -is [string] -and $row.symbol -cmatch '^[A-Z0-9_]{1,32}$') 'CURRENT_ORDER_SYMBOL_INVALID'
  $field='clientOrderId';$stateField='status';$allowed=@('NEW','PARTIALLY_FILLED','PENDING_CANCEL');if($Role -ceq 'ALGO'){$field='clientAlgoId';$stateField='algoStatus';$allowed=@('NEW','WORKING','TRIGGERING','PENDING_CANCEL')}
  $id=Inspect-Member $row $field;$state=Inspect-Member $row $stateField
  Inspect-Require ($id -is [string] -and $id.Length -gt 0 -and $id.Length -le 128 -and $state -is [string] -and $allowed -ccontains $state) 'CURRENT_OPEN_ORDER_SCHEMA_INVALID'
  $hash=Inspect-Hash ([Text.Encoding]::UTF8.GetBytes($id));Inspect-Require (-not $ids.ContainsKey($hash)) 'CURRENT_DUPLICATE_OPEN_ID';$ids[$hash]=$true
  if($row.symbol -cne 'BTCUSDT'){$other++}
 }
 return [pscustomobject]@{count=$list.returned_count;non_btc_count=$other;empty=($list.returned_count -eq 0)}
}
function Current-Positions([string]$Body) {
 $list=Read-List $Body;$keys=@{};$nonzero=0;$other=0;$hedged=0
 foreach($row in $list.rows){
  Inspect-Require ($row.symbol -is [string] -and $row.symbol -cmatch '^[A-Z0-9_]{1,32}$' -and @('BOTH','LONG','SHORT') -ccontains $row.positionSide) 'CURRENT_POSITION_SCHEMA_INVALID'
  $key=$row.symbol+':'+$row.positionSide;Inspect-Require (-not $keys.ContainsKey($key)) 'CURRENT_DUPLICATE_POSITION';$keys[$key]=$true
  $qty=Income-Decimal $row.positionAmt;if($row.positionSide -cne 'BOTH'){$hedged++};if($qty -ne 0){$nonzero++;if($row.symbol -cne 'BTCUSDT'){$other++}}
 }
 return [pscustomobject]@{returned_rows=$list.returned_count;nonzero_positions=$nonzero;nonzero_non_btc_positions=$other;hedge_side_rows=$hedged;flat=($nonzero -eq 0)}
}
function Current-Account([string]$Body) {
 $base=Income-Account $Body;$data=Export-Decode $Body;$usdt=@($data.assets|Where-Object {$_.asset -ceq 'USDT'})[0]
 $margin=Income-Decimal $data.totalInitialMargin;$allPnl=Income-Decimal $data.totalUnrealizedProfit;$available=Income-Decimal $usdt.availableBalance;$assetMargin=Income-Decimal $usdt.marginBalance
 Inspect-Require ($margin -ge 0 -and $available -ge 0) 'CURRENT_ACCOUNT_MARGIN_INVALID'
 return [pscustomobject]@{usdt_wallet=(Income-Text $base.wallet);usdt_available=(Income-Text $available);usdt_margin_balance=(Income-Text $assetMargin);total_initial_margin=(Income-Text $margin);total_unrealized_profit=(Income-Text $allPnl);positions_flat=$base.positions_flat;flat_unencumbered_usdt=($base.positions_flat -and $margin -eq 0 -and $allPnl -eq 0 -and $base.usdt_unrealized -eq 0 -and $available -eq $base.wallet -and $assetMargin -eq $base.wallet)}
}
function Current-Fetch([object]$Context,[string]$Directory,[string]$Name,[string]$Path,[hashtable]$Params) {
 $reply=Export-Http $Context $Path $Params;$hash=Income-Save (Join-Path $Directory ($Name+'.json')) $reply.body
 return [pscustomobject]@{body=$reply.body;file=($Name+'.json');sha256=$hash;route=$Path}
}
function Invoke-CurrentRead([object]$Plan,[object]$Source) {
 $context=[pscustomobject]@{credentials=$null;watch=[Diagnostics.Stopwatch]::StartNew();server_time=$null;clock_observed_ms=0L;http_attempts=0;max_used_weight=0L;weight_observed=$false;weight_limit=0L}
 $directory=$null;$script:ReferenceAttemptPath=$null;$failure=$null;$evidence=@();$rounds=@();$modeBefore=$null;$modeAfter=$null;$configBefore=$null;$configAfter=$null;$issues=@();$summary=$null;$sourcesVerified=$false;$observationEnd=$null
 try {
  Inspect-Require (($null -eq $env:LIVE_TRADING_ENABLED -or $env:LIVE_TRADING_ENABLED -ieq 'false') -and $env:TRADING_MODE -ine 'LIVE') 'CURRENT_LIVE_CONTEXT_FORBIDDEN'
  foreach($name in @('BINANCE_DEMO_API_KEY','BINANCE_DEMO_API_SECRET','BINANCE_API_KEY','BINANCE_API_SECRET','BINANCE_LIVE_API_KEY','BINANCE_LIVE_API_SECRET')){Inspect-Require ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name))) 'CURRENT_ENV_CREDENTIALS_FORBIDDEN'}
  Inspect-Require ($Plan.policy -ceq 'DEMO_INCOME_READ_20261118_V1' -and $Plan.host -ceq 'https://demo-fapi.binance.com' -and @($Plan.anchors).Count -eq 2 -and @($Plan.anchors.server_id|Select-Object -Unique).Count -eq 2) 'CURRENT_PLAN_INVALID'
  Trades-VerifySaved $Source;$directory=Reference-NewDirectory ([Guid]::NewGuid().ToString('N'))
  Export-WriteJson (Join-Path $directory 'INTENT.json') ([ordered]@{policy='DEMO_CURRENT_ACCOUNT_READ_20261118_V1';manifest_sha256=$Plan.manifest_sha256;saved_income_receipt_sha256=$Source.receipt_sha256;saved_trade_receipt_sha256=$Source.trade_receipt_sha256;export_jobs_allowed=0;automatic_retry=$false})
  $context.credentials=Export-Credentials;$context.watch.Restart();Export-Clock $context
  $keys=Income-Boundaries $context.server_time $Plan.week_start_kst
  Inspect-Require ($keys.day_key -ceq $Source.day_key -and $keys.week_key -ceq $Source.week_key -and $context.server_time -ge $Source.trade_end_ms+5000L) 'CURRENT_SAVED_PERIOD_OR_WINDOW_STALE'
  Export-RateLimit $context
  foreach($anchor in $Plan.anchors){$reply=Export-Http $context '/fapi/v1/order' @{symbol='BTCUSDT';orderId=[string]$anchor.server_id};Export-Anchor $anchor (Export-Decode $reply.body);Inspect-Require $reply.weight_observed 'CURRENT_RATE_HEADER_REQUIRED'}
  Inspect-Require ($context.weight_observed -and $context.max_used_weight+500 -le $context.weight_limit) 'CURRENT_INSUFFICIENT_WEIGHT_HEADROOM'
  $raw=Current-Fetch $context $directory 'MODE_BEFORE' '/fapi/v1/accountConfig' @{};$evidence+=,[pscustomobject]@{file=$raw.file;sha256=$raw.sha256;route=$raw.route};$modeBefore=Current-Mode $raw.body
  $raw=Current-Fetch $context $directory 'BTC_CONFIG_BEFORE' '/fapi/v1/symbolConfig' @{symbol='BTCUSDT'};$evidence+=,[pscustomobject]@{file=$raw.file;sha256=$raw.sha256;route=$raw.route};$configBefore=Current-BtcConfig $raw.body
  foreach($index in 1..2){
   $raw=Current-Fetch $context $directory ('REGULAR_OPEN_'+$index) '/fapi/v1/openOrders' @{};$evidence+=,[pscustomobject]@{file=$raw.file;sha256=$raw.sha256;route=$raw.route};$regular=Current-Orders $raw.body 'REGULAR'
   $raw=Current-Fetch $context $directory ('ALGO_OPEN_'+$index) '/fapi/v1/openAlgoOrders' @{};$evidence+=,[pscustomobject]@{file=$raw.file;sha256=$raw.sha256;route=$raw.route};$algo=Current-Orders $raw.body 'ALGO'
   $raw=Current-Fetch $context $directory ('POSITIONS_'+$index) '/fapi/v3/positionRisk' @{};$evidence+=,[pscustomobject]@{file=$raw.file;sha256=$raw.sha256;route=$raw.route};$positions=Current-Positions $raw.body
   $raw=Current-Fetch $context $directory ('ACCOUNT_'+$index) '/fapi/v3/account' @{};$evidence+=,[pscustomobject]@{file=$raw.file;sha256=$raw.sha256;route=$raw.route};$account=Current-Account $raw.body
   $rounds+=,[pscustomobject]@{regular_orders=$regular;algo_orders=$algo;positions=$positions;account=$account}
  }
  $raw=Current-Fetch $context $directory 'MODE_AFTER' '/fapi/v1/accountConfig' @{};$evidence+=,[pscustomobject]@{file=$raw.file;sha256=$raw.sha256;route=$raw.route};$modeAfter=Current-Mode $raw.body
  $raw=Current-Fetch $context $directory 'BTC_CONFIG_AFTER' '/fapi/v1/symbolConfig' @{symbol='BTCUSDT'};$evidence+=,[pscustomobject]@{file=$raw.file;sha256=$raw.sha256;route=$raw.route};$configAfter=Current-BtcConfig $raw.body
  $observationEnd=Income-Now $context
  $raw=Current-Fetch $context $directory 'INCOME_GAP' '/fapi/v1/income' @{startTime=[string]($Source.old_cutoff_ms+1L);endTime=[string]$observationEnd;page='1';limit='1000'};$evidence+=,[pscustomobject]@{file=$raw.file;sha256=$raw.sha256;route=$raw.route};$incomeGap=Read-List $raw.body;$seenIncome=@{};$null=Income-AddRows $incomeGap.rows $seenIncome ($Source.old_cutoff_ms+1L) $observationEnd
  $raw=Current-Fetch $context $directory 'BTC_TRADES_GAP' '/fapi/v1/userTrades' @{symbol='BTCUSDT';startTime=[string]($Source.trade_end_ms+1L);endTime=[string]$observationEnd;limit='1000'};$evidence+=,[pscustomobject]@{file=$raw.file;sha256=$raw.sha256;route=$raw.route};$tradeGap=Read-List $raw.body;$seenTrades=@{};$null=Trades-Add $tradeGap.rows $seenTrades ($Source.trade_end_ms+1L) $observationEnd
  if(-not $modeBefore.one_way -or -not $modeAfter.one_way){$issues+='ACCOUNT_HEDGE_MODE'}
  if(-not $modeBefore.single_asset -or -not $modeAfter.single_asset){$issues+='ACCOUNT_MULTI_ASSET_MODE'}
  if(-not $modeBefore.account_can_trade -or -not $modeAfter.account_can_trade){$issues+='ACCOUNT_PERMISSION_CANNOT_TRADE'}
  if(-not $configBefore.expected_isolated_2x_no_auto_add -or -not $configAfter.expected_isolated_2x_no_auto_add){$issues+='BTC_SETTINGS_DO_NOT_MATCH_ISOLATED_2X_NO_AUTO_ADD'}
  $modeStable=($modeBefore|ConvertTo-Json -Compress) -ceq ($modeAfter|ConvertTo-Json -Compress);$configStable=($configBefore|ConvertTo-Json -Compress) -ceq ($configAfter|ConvertTo-Json -Compress)
  if(-not $modeStable -or -not $configStable){$issues+='SETTINGS_CHANGED_DURING_READ'}
  foreach($round in $rounds){if(-not $round.regular_orders.empty){$issues+='REGULAR_OPEN_ORDERS_PRESENT'};if(-not $round.algo_orders.empty){$issues+='ALGO_OPEN_ORDERS_PRESENT'};if(-not $round.positions.flat -or -not $round.account.positions_flat){$issues+='CURRENT_POSITIONS_PRESENT'};if($round.positions.hedge_side_rows -gt 0){$issues+='POSITION_RISK_HEDGE_SIDE_ROWS'};if(-not $round.account.flat_unencumbered_usdt){$issues+='MARGIN_OR_USDT_BALANCE_NOT_FLAT_UNENCUMBERED'};if($round.account.usdt_wallet -cne $Source.wallet){$issues+='CURRENT_WALLET_DIFFERS_FROM_SAVED_CANDIDATE'}}
  $accountStable=($rounds[0].account|ConvertTo-Json -Compress) -ceq ($rounds[1].account|ConvertTo-Json -Compress)
  if(-not $accountStable){$issues+='ACCOUNT_CHANGED_DURING_READ'}
  if($incomeGap.returned_count -gt 0){$issues+='INCOME_ACTIVITY_GAP_NOT_EMPTY'};if($tradeGap.returned_count -gt 0){$issues+='BTC_TRADE_ACTIVITY_GAP_NOT_EMPTY'}
  if($incomeGap.page_limit_reached -or $tradeGap.page_limit_reached){$issues+='ACTIVITY_GAP_SATURATED_NO_COMPLETE_COVERAGE'}
  $last=Income-Boundaries (Income-Now $context) $Plan.week_start_kst;Inspect-Require ($last.day_key -ceq $Source.day_key -and $last.week_key -ceq $Source.week_key) 'CURRENT_PERIOD_CHANGED_DURING_READ'
  Trades-VerifySaved $Source;$sourcesVerified=$true
  $summary=[pscustomobject]@{mode_before=$modeBefore;mode_after=$modeAfter;btc_config_before=$configBefore;btc_config_after=$configAfter;rounds=$rounds;mode_equal_at_reads=$modeStable;btc_config_equal_at_reads=$configStable;account_equal_at_reads=$accountStable;income_gap_rows=$incomeGap.returned_count;btc_trade_gap_rows=$tradeGap.returned_count;income_gap_page_limit_reached=$incomeGap.page_limit_reached;trade_gap_page_limit_reached=$tradeGap.page_limit_reached;observations_consistent_with_expected_flat_profile=($issues.Count -eq 0);issues=@($issues|Select-Object -Unique)}
  $state='CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES'
 } catch {
  if($null -eq $directory -and $null -ne $script:ReferenceAttemptPath){$directory=$script:ReferenceAttemptPath}
  $failure='CURRENT_RUN_BLOCKED';if($_.Exception.Message -cmatch '^(CURRENT|TRADES|INCOME|READ|EXPORT|INSPECT|REFERENCE)_[A-Z0-9_]{1,100}$'){$failure=$_.Exception.Message};$state='STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY'
 } finally {if($null -ne $context.credentials){$context.credentials.key=$null;$context.credentials.secret=$null;$context.credentials=$null};$context.watch.Stop()}
 $result=[pscustomobject]@{current_read=$state;failure_category=$failure;private_directory=$directory;day_key_kst=$Source.day_key;week_key_kst=$Source.week_key;saved_wallet_candidate_usdt=$Source.wallet;observations_end_ms=$observationEnd;summary=$summary;raw_evidence=$evidence;saved_source_hashes_verified_before_after=$sourcesVerified;open_order_and_current_position_scope='ALL_SYMBOLS';btc_configuration_and_trade_history_scope='BTCUSDT';non_atomic_read_sequence=$true;account_wide_historical_trade_history_verified=$false;boundary_positions_independently_verified=$false;equity_baseline_reconstructed=$false;historic_requests_resolved=$false;settings_changed_by_tool=$false;actual_http_attempts=$context.http_attempts;export_jobs_created=0;old_export_guard_changed=$false;automatic_retry=$false;credentials_saved=$false;source_files_written=$false;request_states_changed=$false;baseline_adopted=$false;docker_used=$false;services_started=$false;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;external_ip_usage_fully_known=$false}
 if($null -ne $directory -and [IO.Directory]::Exists($directory)){try{Export-WriteJson (Join-Path $directory 'RECEIPT.json') $result}catch{$result.current_read='STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY';$result.failure_category='CURRENT_RECEIPT_WRITE_FAILED'}}
 return $result
}
