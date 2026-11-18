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
