function Trades-Add([object[]]$Rows,[hashtable]$Seen,[long]$Start,[long]$End) {
 $added=0;$duplicates=0
 foreach($row in $Rows){
  Inspect-Require ($row.symbol -ceq 'BTCUSDT' -and @('BUY','SELL') -ccontains $row.side -and @('BOTH','LONG','SHORT') -ccontains $row.positionSide) 'TRADES_ROW_SCOPE_INVALID'
  $id=Income-Integer $row.id 'TRADES_ID_INVALID';$order=Income-Integer $row.orderId 'TRADES_ORDER_ID_INVALID';$timeText=Income-Integer $row.time 'TRADES_TIME_INVALID'
  Inspect-Require ($id -cmatch '^[0-9]{1,19}$' -and $order -cmatch '^[1-9][0-9]{0,18}$' -and $timeText -cmatch '^[0-9]{13}$') 'TRADES_ID_OR_TIME_INVALID'
  $stamp=[long]$timeText;Inspect-Require ($stamp -ge $Start -and $stamp -le $End) 'TRADES_ROW_OUTSIDE_WINDOW'
  $qty=Income-Decimal $row.qty;$price=Income-Decimal $row.price;$pnl=Income-Decimal $row.realizedPnl;$fee=Income-Decimal $row.commission
  Inspect-Require ($qty -gt 0 -and $price -gt 0 -and $row.commissionAsset -is [string] -and $row.commissionAsset -cmatch '^[A-Z0-9]{2,16}$') 'TRADES_FINANCIAL_ROW_INVALID'
  $key='BTCUSDT:'+$id;$canonical=$order+'|'+$timeText+'|'+$row.side+'|'+$row.positionSide+'|'+(Income-Text $qty)+'|'+(Income-Text $price)+'|'+(Income-Text $pnl)+'|'+(Income-Text $fee)+'|'+$row.commissionAsset
  if($Seen.ContainsKey($key)){Inspect-Require ($Seen[$key].canonical -ceq $canonical) 'TRADES_CONFLICTING_ID';$duplicates++}
  else{$Seen[$key]=[pscustomobject]@{canonical=$canonical;stamp=$stamp;qty=$qty;pnl=$pnl;fee=$fee;fee_asset=$row.commissionAsset;side=$row.side};$added++}
 }
 return [pscustomobject]@{added=$added;duplicates=$duplicates}
}
