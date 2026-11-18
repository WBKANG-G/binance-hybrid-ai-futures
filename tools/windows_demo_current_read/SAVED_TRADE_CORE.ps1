function Add-SavedTrade([object]$Source,[object]$Plan,[string]$Parent) {
 Reference-AssertPrivateDirectory $Parent
 $folders=@(Get-ChildItem -LiteralPath $Parent -Directory -Filter 'trade-read-20261118-*');Inspect-Require ($folders.Count -le 32) 'CURRENT_SAVED_TRADE_SEARCH_LIMIT';$selected=@()
 foreach($folder in $folders){
  Inspect-Require ($folder.Name -cmatch '^trade-read-20261118-[0-9a-f]{32}$') 'CURRENT_SAVED_TRADE_DIRECTORY_INVALID';Reference-AssertPrivateDirectory $folder.FullName
  $path=Join-Path $folder.FullName 'RECEIPT.json';if(-not [IO.File]::Exists($path)){continue}
  $receiptFile=Trades-Open $path 1048576 $Source.streams;$receipt=Inspect-Json $receiptFile.body
  if($receipt.trade_read -cne 'BTC_TRADE_RESPONSES_SAVED_NO_BASELINE_ADOPTION'){continue}
  if($receipt.summary.unique_trade_rows -ne 0 -or $receipt.day_key_kst -cne $Source.day_key -or $receipt.week_key_kst -cne $Source.week_key){continue}
  Inspect-Require ([string]::Equals($receipt.private_directory,$folder.FullName,[StringComparison]::OrdinalIgnoreCase) -and $receipt.symbol_scope -ceq 'BTCUSDT' -and $receipt.actual_http_attempts -eq 5 -and $receipt.export_jobs_created -eq 0 -and $receipt.order_writes -eq 0 -and $receipt.saved_income_cutoff_ms -eq $Source.old_cutoff_ms -and (Income-Decimal $receipt.saved_wallet_candidate_usdt) -eq (Income-Decimal $Source.wallet)) 'CURRENT_SAVED_TRADE_SCOPE_INVALID'
  foreach($name in @('endpoint_window_pagination_finished','saved_income_hashes_verified_before_after','saved_positions_flat')){Inspect-Require ($receipt.$name -is [bool] -and $receipt.$name) 'CURRENT_SAVED_TRADE_STATE_INVALID'}
  foreach($name in @('automatic_retry','credentials_saved','source_files_written','request_states_changed','baseline_adopted','orders_enabled','live_trading_enabled','account_wide_trade_history_verified','operator_statement_extended_to_new_query_end')){Inspect-Require ($receipt.$name -is [bool] -and -not $receipt.$name) 'CURRENT_SAVED_TRADE_STATE_INVALID'}
  $intentFile=Trades-Open (Join-Path $folder.FullName 'INTENT.json') 16384 $Source.streams;$intent=Inspect-Json $intentFile.body
  Inspect-Require ($intent.policy -ceq 'DEMO_BTC_TRADE_READ_20261118_V1' -and $intent.manifest_sha256 -ceq $Plan.manifest_sha256 -and $intent.saved_income_receipt_sha256 -ceq $Source.receipt_sha256 -and $intent.old_cutoff_ms -eq $Source.old_cutoff_ms -and $intent.start_ms -eq $Source.week_start_ms -and $intent.symbol_scope -ceq 'BTCUSDT' -and $intent.export_jobs_allowed -eq 0) 'CURRENT_SAVED_TRADE_INTENT_INVALID'
  $endText=Income-Integer $receipt.trade_query_end_ms 'CURRENT_SAVED_TRADE_WINDOW_INVALID';Inspect-Require ($endText -cmatch '^[0-9]{13}$') 'CURRENT_SAVED_TRADE_WINDOW_INVALID';$end=[long]$endText
  Inspect-Require ($end -ge $Source.old_cutoff_ms+5000L -and $end-$Source.week_start_ms -le 604800000L -and @($receipt.response_windows).Count -eq 1) 'CURRENT_SAVED_TRADE_WINDOW_INVALID'
  $window=@($receipt.response_windows)[0]
  Inspect-Require ($window.file -ceq 'TRADES_ROOT.json' -and $window.sha256 -cmatch '^[0-9a-f]{64}$' -and $window.start_ms -eq $Source.week_start_ms -and $window.end_ms -eq $end -and $window.returned_count -eq 0) 'CURRENT_SAVED_TRADE_WINDOW_INVALID'
  $raw=Trades-Open (Join-Path $folder.FullName 'TRADES_ROOT.json') 4194304 $Source.streams
  Inspect-Require ($raw.sha256 -ceq $window.sha256 -and (Read-List $raw.body).returned_count -eq 0) 'CURRENT_SAVED_TRADE_HASH_OR_ROWS_INVALID'
  $selected+=,[pscustomobject]@{receipt_sha256=$receiptFile.sha256;end_ms=$end}
 }
 Inspect-Require ($selected.Count -eq 1) 'CURRENT_SAVED_TRADE_NOT_UNIQUE'
 $Source|Add-Member NoteProperty trade_receipt_sha256 $selected[0].receipt_sha256
 $Source|Add-Member NoteProperty trade_end_ms $selected[0].end_ms
 Trades-VerifySaved $Source
}
