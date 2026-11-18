function Income-Integer([object]$Value,[string]$Code) {
 Inspect-Require ($null -ne $Value -and ($Value -is [string] -or $Value -is [int] -or $Value -is [long] -or $Value -is [decimal])) $Code
 $text=[Convert]::ToString($Value,[Globalization.CultureInfo]::InvariantCulture)
 Inspect-Require ($text -cmatch '^[0-9]{1,20}$') $Code
 return $text
}
function Income-Decimal([object]$Value) {
 Inspect-Require ($Value -is [string] -and $Value -cmatch '^-?[0-9]{1,12}(\.[0-9]{1,8})?$') 'INCOME_AMOUNT_INVALID'
 try {return [decimal]::Parse($Value,[Globalization.CultureInfo]::InvariantCulture)} catch {throw 'INCOME_AMOUNT_INVALID'}
}
function Income-Text([decimal]$Value) {return $Value.ToString('0.00000000',[Globalization.CultureInfo]::InvariantCulture)}
function Income-Now([object]$Context) {return [long]$Context.server_time+[long]$Context.watch.ElapsedMilliseconds-[long]$Context.clock_observed_ms}
function Income-Boundaries([long]$Now,[int]$WeekStart) {
 Inspect-Require ($WeekStart -ge 0 -and $WeekStart -le 6) 'INCOME_WEEK_POLICY_INVALID'
 $offset=[TimeSpan]::FromHours(9);$kst=[DateTimeOffset]::FromUnixTimeMilliseconds($Now).ToOffset($offset)
 $day=New-Object DateTimeOffset($kst.Date,$offset)
 $weekday=([int]$kst.DayOfWeek+6)%7;$days=($weekday-$WeekStart+7)%7;$week=$day.AddDays(-$days)
 return [pscustomobject]@{day_key=$day.ToString('yyyy-MM-dd',[Globalization.CultureInfo]::InvariantCulture);week_key=$week.ToString('yyyy-MM-dd',[Globalization.CultureInfo]::InvariantCulture);day_ms=$day.ToUnixTimeMilliseconds();week_ms=$week.ToUnixTimeMilliseconds();week_start_kst=$WeekStart}
}
function Income-Save([string]$Path,[string]$Body) {
 $bytes=[Text.Encoding]::UTF8.GetBytes($Body);Inspect-Require ($bytes.Length -le 4194304) 'INCOME_RESPONSE_LIMIT'
 $file=New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try {$file.Write($bytes,0,$bytes.Length);$file.Flush($true)} finally {$file.Dispose()}
 Inspect-Require ((Inspect-Hash ([IO.File]::ReadAllBytes($Path))) -ceq (Inspect-Hash $bytes)) 'INCOME_RESPONSE_READBACK_FAILED'
 return Inspect-Hash $bytes
}
function Income-Account([string]$Body) {
 $data=Export-Decode $Body
 Inspect-Require ($data.GetType() -eq [System.Management.Automation.PSCustomObject] -and $data.assets -is [Array] -and $data.positions -is [Array]) 'INCOME_ACCOUNT_SCHEMA_INVALID'
 $usdt=@($data.assets|Where-Object {$_.asset -ceq 'USDT'});Inspect-Require ($usdt.Count -eq 1) 'INCOME_USDT_ASSET_NOT_UNIQUE'
 $wallet=Income-Decimal $usdt[0].walletBalance;$unrealized=Income-Decimal $usdt[0].unrealizedProfit
 Inspect-Require ($wallet -gt 0) 'INCOME_WALLET_NONPOSITIVE'
 $flat=$unrealized -eq 0
 foreach($position in $data.positions){if((Income-Decimal $position.positionAmt) -ne 0){$flat=$false}}
 return [pscustomobject]@{wallet=$wallet;usdt_unrealized=$unrealized;positions_flat=$flat}
}
function Income-AddRows([object[]]$Rows,[hashtable]$Seen,[long]$Start,[long]$End) {
 $added=0;$duplicates=0
 foreach($row in $Rows){
  $type=$row.incomeType;$asset=$row.asset
  Inspect-Require ($type -is [string] -and $type -cmatch '^[A-Z_]{1,64}$' -and $asset -is [string] -and $asset -cmatch '^[A-Z0-9]{2,16}$') 'INCOME_ROW_SCOPE_INVALID'
  $id=Income-Integer $row.tranId 'INCOME_TRANSACTION_ID_INVALID';$stampText=Income-Integer $row.time 'INCOME_TIME_INVALID'
  Inspect-Require ($id -cmatch '^[1-9][0-9]{0,19}$' -and $stampText -cmatch '^[0-9]{13}$') 'INCOME_TRANSACTION_ID_OR_TIME_INVALID'
  $stamp=[long]$stampText;Inspect-Require ($stamp -ge $Start -and $stamp -le $End) 'INCOME_ROW_OUTSIDE_WINDOW'
  $amount=Income-Decimal $row.income;$symbol=Inspect-Member $row 'symbol'
  Inspect-Require ($null -eq $symbol -or ($symbol -is [string] -and $symbol -cmatch '^[A-Z0-9_]{0,32}$')) 'INCOME_SYMBOL_INVALID'
  $key=$type+':'+$id;$canonical=$asset+'|'+(Income-Text $amount)+'|'+$stampText+'|'+[string]$symbol
  if($Seen.ContainsKey($key)){Inspect-Require ($Seen[$key].canonical -ceq $canonical) 'INCOME_CONFLICTING_TRANSACTION';$duplicates++}
  else {$Seen[$key]=[pscustomobject]@{canonical=$canonical;income_type=$type;asset=$asset;amount=$amount;stamp=$stamp};$added++}
 }
 return [pscustomobject]@{added=$added;duplicates=$duplicates}
}
function Income-Summary([hashtable]$Seen,[object]$Boundaries) {
 $known=@('TRANSFER','WELCOME_BONUS','REALIZED_PNL','FUNDING_FEE','COMMISSION','INSURANCE_CLEAR','COMMISSION_REBATE','API_REBATE')
 $groups=@{};$week=[decimal]0;$day=[decimal]0;$foreign=0;$unknown=0;$atDay=$false;$atWeek=$false;$usdtRows=0
 foreach($row in $Seen.Values){
  if($row.asset -cne 'USDT'){$foreign++;continue}
  $usdtRows++;$week+=$row.amount
  if($row.stamp -ge $Boundaries.day_ms){$day+=$row.amount}
  if($row.stamp -eq $Boundaries.day_ms){$atDay=$true};if($row.stamp -eq $Boundaries.week_ms){$atWeek=$true}
  $group=$row.income_type;if($known -cnotcontains $group){$group='OTHER_UNREVIEWED';$unknown++}
  if(-not $groups.ContainsKey($group)){$groups[$group]=[pscustomobject]@{income_type=$group;rows=0;usdt_net=[decimal]0}}
  $groups[$group].rows++;$groups[$group].usdt_net+=$row.amount
 }
 $safeGroups=@($groups.Values|Sort-Object income_type|ForEach-Object {[pscustomobject]@{income_type=$_.income_type;rows=$_.rows;usdt_net=(Income-Text $_.usdt_net)}})
 return [pscustomobject]@{unique_rows=$Seen.Count;usdt_rows=$usdtRows;non_usdt_rows=$foreign;unreviewed_usdt_type_rows=$unknown;week_net=$week;day_net=$day;event_at_day_boundary=$atDay;event_at_week_boundary=$atWeek;by_income_type=$safeGroups}
}
function Invoke-IncomeRead([object]$Plan) {
 $context=[pscustomobject]@{credentials=$null;watch=[Diagnostics.Stopwatch]::StartNew();server_time=$null;clock_observed_ms=0L;http_attempts=0;max_used_weight=0L;weight_observed=$false;weight_limit=0L}
 $directory=$null;$script:ReferenceAttemptPath=$null;$failure=$null;$evidence=@();$duplicates=0;$summary=$null;$boundary=$null;$wallet=$null;$dayCandidate=$null;$weekCandidate=$null;$stable=$false;$tailEmpty=$false;$flat=$false;$finished=$false
 try {
  Inspect-Require (($null -eq $env:LIVE_TRADING_ENABLED -or $env:LIVE_TRADING_ENABLED -ieq 'false') -and $env:TRADING_MODE -ine 'LIVE') 'INCOME_LIVE_CONTEXT_FORBIDDEN'
  foreach($name in @('BINANCE_DEMO_API_KEY','BINANCE_DEMO_API_SECRET','BINANCE_API_KEY','BINANCE_API_SECRET','BINANCE_LIVE_API_KEY','BINANCE_LIVE_API_SECRET')){Inspect-Require ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name))) 'INCOME_ENV_CREDENTIALS_FORBIDDEN'}
  Inspect-Require ($Plan.policy -ceq 'DEMO_INCOME_READ_20261118_V1' -and $Plan.host -ceq 'https://demo-fapi.binance.com' -and @($Plan.anchors).Count -eq 2 -and @($Plan.anchors.server_id|Select-Object -Unique).Count -eq 2 -and $Plan.week_start_kst -ge 0 -and $Plan.week_start_kst -le 6) 'INCOME_PLAN_INVALID'
  $directory=Reference-NewDirectory ([Guid]::NewGuid().ToString('N'))
  Export-WriteJson (Join-Path $directory 'INTENT.json') ([ordered]@{policy='DEMO_INCOME_READ_INTENT_V1';manifest_sha256=$Plan.manifest_sha256;week_start_kst=$Plan.week_start_kst;export_jobs_allowed=0;automatic_retry=$false})
  $context.credentials=Export-Credentials;$context.watch.Restart();Export-Clock $context;Export-RateLimit $context
  foreach($anchor in $Plan.anchors){$reply=Export-Http $context '/fapi/v1/order' @{symbol='BTCUSDT';orderId=[string]$anchor.server_id};Export-Anchor $anchor (Export-Decode $reply.body);Inspect-Require $reply.weight_observed 'INCOME_CURRENT_RATE_HEADER_REQUIRED'}
  Inspect-Require ($context.weight_observed -and $context.max_used_weight+300 -le $context.weight_limit) 'INCOME_INSUFFICIENT_WEIGHT_HEADROOM'
  $beforeStart=Income-Now $context;$reply=Export-Http $context '/fapi/v3/account' @{}
  $hash=Income-Save (Join-Path $directory 'ACCOUNT_BEFORE.json') $reply.body;$evidence+=,[pscustomobject]@{file='ACCOUNT_BEFORE.json';sha256=$hash}
  $before=Income-Account $reply.body;$cutoff=Income-Now $context;$boundary=Income-Boundaries $cutoff $Plan.week_start_kst
  Export-WriteJson (Join-Path $directory 'WINDOW.json') ([ordered]@{day_key_kst=$boundary.day_key;week_key_kst=$boundary.week_key;day_boundary_ms=$boundary.day_ms;week_boundary_ms=$boundary.week_ms;income_cutoff_ms=$cutoff;first_account_request_start_ms=$beforeStart;cutoff_is_client_server_clock_estimate=$true})
  $seen=@{}
  for($page=1;$page -le 3;$page++){
   $reply=Export-Http $context '/fapi/v1/income' @{startTime=[string]$boundary.week_ms;endTime=[string]$cutoff;page=[string]$page;limit='1000'}
   $name='INCOME_PAGE_'+$page+'.json';$hash=Income-Save (Join-Path $directory $name) $reply.body;$evidence+=,[pscustomobject]@{file=$name;sha256=$hash}
   $list=Read-List $reply.body;$progress=Income-AddRows $list.rows $seen $boundary.week_ms $cutoff;$duplicates+=$progress.duplicates
   if(-not $list.page_limit_reached){$finished=$true;break}
   Inspect-Require ($progress.added -gt 0) 'INCOME_PAGINATION_NO_PROGRESS'
  }
  Inspect-Require $finished 'INCOME_PAGINATION_INCOMPLETE'
  $reply=Export-Http $context '/fapi/v3/account' @{};$hash=Income-Save (Join-Path $directory 'ACCOUNT_AFTER.json') $reply.body;$evidence+=,[pscustomobject]@{file='ACCOUNT_AFTER.json';sha256=$hash}
  $after=Income-Account $reply.body;$afterEnd=Income-Now $context
  $reply=Export-Http $context '/fapi/v1/income' @{startTime=[string]$beforeStart;endTime=[string]$afterEnd;page='1';limit='1000'}
  $hash=Income-Save (Join-Path $directory 'COLLECTION_INTERVAL_INCOME.json') $reply.body;$evidence+=,[pscustomobject]@{file='COLLECTION_INTERVAL_INCOME.json';sha256=$hash}
  $tail=Read-List $reply.body;$tailEmpty=$tail.returned_count -eq 0
  $tailSeen=@{};$null=Income-AddRows $tail.rows $tailSeen $beforeStart $afterEnd
  $stable=$before.wallet -eq $after.wallet;$flat=$before.positions_flat -and $after.positions_flat
  $wallet=Income-Text $after.wallet;$summary=Income-Summary $seen $boundary
  Inspect-Require ((Income-Boundaries $afterEnd $Plan.week_start_kst).day_key -ceq $boundary.day_key -and (Income-Boundaries $afterEnd $Plan.week_start_kst).week_key -ceq $boundary.week_key) 'INCOME_PERIOD_CHANGED_DURING_COLLECTION'
  if($stable -and $tailEmpty -and $summary.unreviewed_usdt_type_rows -eq 0){
   $day=$after.wallet-$summary.day_net;$week=$after.wallet-$summary.week_net
   if(-not $summary.event_at_day_boundary -and $day -gt 0){$dayCandidate=Income-Text $day}
   if(-not $summary.event_at_week_boundary -and $week -gt 0){$weekCandidate=Income-Text $week}
  }
  $state='INCOME_RESPONSES_SAVED_WALLET_CANDIDATES_ONLY'
 } catch {
  if($null -eq $directory -and $null -ne $script:ReferenceAttemptPath){$directory=$script:ReferenceAttemptPath}
  $failure='INCOME_RUN_BLOCKED';if($_.Exception.Message -cmatch '^(INCOME|READ|EXPORT|INSPECT|REFERENCE)_[A-Z0-9_]{1,100}$'){$failure=$_.Exception.Message};$state='STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY';$dayCandidate=$null;$weekCandidate=$null
 } finally {if($null -ne $context.credentials){$context.credentials.key=$null;$context.credentials.secret=$null;$context.credentials=$null};$context.watch.Stop()}
 $safeSummary=$null
 if($null -ne $summary){$safeSummary=[pscustomobject]@{unique_rows=$summary.unique_rows;usdt_rows=$summary.usdt_rows;non_usdt_rows=$summary.non_usdt_rows;unreviewed_usdt_type_rows=$summary.unreviewed_usdt_type_rows;week_net_usdt=(Income-Text $summary.week_net);day_net_usdt=(Income-Text $summary.day_net);event_at_day_boundary=$summary.event_at_day_boundary;event_at_week_boundary=$summary.event_at_week_boundary;by_income_type=$summary.by_income_type}}
 $result=[pscustomobject]@{income_read=$state;failure_category=$failure;private_directory=$directory;boundaries=$boundary;current_usdt_wallet=$wallet;day_wallet_candidate_usdt=$dayCandidate;week_wallet_candidate_usdt=$weekCandidate;summary=$safeSummary;duplicate_income_rows_ignored=$duplicates;income_pagination_finished=$finished;wallet_equal_at_snapshots=$stable;collection_interval_income_empty=$tailEmpty;positions_flat_at_snapshots=$flat;boundary_positions_verified=$false;equity_baseline_reconstructed=$false;history_completeness_independently_verified=$false;raw_evidence=$evidence;actual_http_attempts=$context.http_attempts;export_jobs_created=0;old_export_guard_changed=$false;automatic_retry=$false;credentials_saved=$false;source_files_written=$false;request_states_changed=$false;baseline_adopted=$false;docker_used=$false;services_started=$false;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
 if($null -ne $directory -and [IO.Directory]::Exists($directory)){try{Export-WriteJson (Join-Path $directory 'RECEIPT.json') $result}catch{$result.income_read='STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY';$result.failure_category='INCOME_RECEIPT_WRITE_FAILED';$result.day_wallet_candidate_usdt=$null;$result.week_wallet_candidate_usdt=$null}}
 return $result
}
