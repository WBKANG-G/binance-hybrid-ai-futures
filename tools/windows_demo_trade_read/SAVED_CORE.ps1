function Trades-Open([string]$Path,[int]$Limit,[object]$Streams) {
 $stream=Inspect-Open $Path $Limit;$entry=[pscustomobject]@{stream=$stream;sha256=$null;body=$null};$null=$Streams.Add($entry)
 $entry.sha256=Inspect-StreamHash $stream;$reader=New-Object IO.StreamReader($stream,(New-Object Text.UTF8Encoding($false,$true)),$true,1024,$true)
 try {$entry.body=$reader.ReadToEnd()} finally {$reader.Dispose()};return $entry
}
function Trades-VerifySaved([object]$Source) {foreach($entry in $Source.streams){Inspect-Require ((Inspect-StreamHash $entry.stream) -ceq $entry.sha256) 'TRADES_SAVED_INPUT_CHANGED'}}
function Trades-CloseSaved([object]$Source) {if($null -ne $Source){foreach($entry in $Source.streams){$entry.stream.Dispose()}}}
function Get-SavedIncome([object]$Plan,[string]$Parent) {
 $streams=New-Object Collections.ArrayList
 try {
  Reference-AssertPrivateDirectory $Parent
  $folders=@(Get-ChildItem -LiteralPath $Parent -Directory -Filter 'income-read-20261118-*');Inspect-Require ($folders.Count -le 32) 'TRADES_SAVED_SEARCH_LIMIT';$selected=@()
  foreach($folder in $folders){
   Inspect-Require ($folder.Name -cmatch '^income-read-20261118-[0-9a-f]{32}$') 'TRADES_SAVED_DIRECTORY_INVALID';Reference-AssertPrivateDirectory $folder.FullName
   $receiptPath=Join-Path $folder.FullName 'RECEIPT.json';if(-not [IO.File]::Exists($receiptPath)){continue}
   $receiptFile=Trades-Open $receiptPath 1048576 $streams;$receipt=Inspect-Json $receiptFile.body
   if($receipt.income_read -cne 'INCOME_RESPONSES_SAVED_WALLET_CANDIDATES_ONLY'){continue}
   if($receipt.boundaries.day_key -cne '2026-11-18' -or $receipt.boundaries.week_key -cne '2026-11-16' -or $receipt.summary.unique_rows -ne 0){continue}
   Inspect-Require ([string]::Equals($receipt.private_directory,$folder.FullName,[StringComparison]::OrdinalIgnoreCase) -and $receipt.actual_http_attempts -eq 8 -and $receipt.export_jobs_created -eq 0 -and $receipt.order_writes -eq 0) 'TRADES_SAVED_RECEIPT_SCOPE_INVALID'
   foreach($name in @('income_pagination_finished','wallet_equal_at_snapshots','collection_interval_income_empty','positions_flat_at_snapshots')){Inspect-Require ($receipt.$name -is [bool] -and $receipt.$name) 'TRADES_SAVED_RECEIPT_STATE_INVALID'}
   foreach($name in @('automatic_retry','credentials_saved','source_files_written','request_states_changed','baseline_adopted','orders_enabled','live_trading_enabled')){Inspect-Require ($receipt.$name -is [bool] -and -not $receipt.$name) 'TRADES_SAVED_RECEIPT_STATE_INVALID'}
   $intentFile=Trades-Open (Join-Path $folder.FullName 'INTENT.json') 16384 $streams;$intent=Inspect-Json $intentFile.body
   Inspect-Require ($intent.policy -ceq 'DEMO_INCOME_READ_INTENT_V1' -and $intent.manifest_sha256 -ceq $Plan.manifest_sha256 -and $intent.week_start_kst -eq $Plan.week_start_kst -and $intent.export_jobs_allowed -eq 0) 'TRADES_SAVED_INTENT_INVALID'
   $windowFile=Trades-Open (Join-Path $folder.FullName 'WINDOW.json') 16384 $streams;$window=Inspect-Json $windowFile.body
   $startText=Income-Integer $window.week_boundary_ms 'TRADES_SAVED_WINDOW_INVALID';$endText=Income-Integer $window.income_cutoff_ms 'TRADES_SAVED_WINDOW_INVALID'
   Inspect-Require ($startText -cmatch '^[0-9]{13}$' -and $endText -cmatch '^[0-9]{13}$') 'TRADES_SAVED_WINDOW_INVALID'
   $start=[long]$startText;$cutoff=[long]$endText;$bounds=Income-Boundaries $cutoff $Plan.week_start_kst
   Inspect-Require ($window.day_key_kst -ceq $bounds.day_key -and $window.week_key_kst -ceq $bounds.week_key -and $bounds.day_key -ceq '2026-11-18' -and $bounds.week_key -ceq '2026-11-16' -and $window.day_boundary_ms -eq $bounds.day_ms -and $start -eq $bounds.week_ms -and $receipt.boundaries.day_ms -eq $bounds.day_ms -and $receipt.boundaries.week_ms -eq $bounds.week_ms) 'TRADES_SAVED_WINDOW_INVALID'
   $names=@($receipt.raw_evidence|ForEach-Object {$_.file}|Sort-Object)
   Inspect-Require (($names -join '|') -ceq 'ACCOUNT_AFTER.json|ACCOUNT_BEFORE.json|COLLECTION_INTERVAL_INCOME.json|INCOME_PAGE_1.json') 'TRADES_SAVED_EVIDENCE_SCOPE_INVALID'
   $raw=@{}
   foreach($item in $receipt.raw_evidence){Inspect-Require ($item.sha256 -cmatch '^[0-9a-f]{64}$') 'TRADES_SAVED_HASH_INVALID';$file=Trades-Open (Join-Path $folder.FullName $item.file) 4194304 $streams;Inspect-Require ($file.sha256 -ceq $item.sha256) 'TRADES_SAVED_HASH_MISMATCH';$raw[$item.file]=$file}
   Inspect-Require ((Read-List $raw['INCOME_PAGE_1.json'].body).returned_count -eq 0 -and (Read-List $raw['COLLECTION_INTERVAL_INCOME.json'].body).returned_count -eq 0) 'TRADES_SAVED_INCOME_NOT_EMPTY'
   $before=Income-Account $raw['ACCOUNT_BEFORE.json'].body;$after=Income-Account $raw['ACCOUNT_AFTER.json'].body
   Inspect-Require ($before.wallet -eq $after.wallet -and $before.positions_flat -and $after.positions_flat -and (Income-Decimal $receipt.current_usdt_wallet) -eq $after.wallet -and (Income-Decimal $receipt.day_wallet_candidate_usdt) -eq $after.wallet -and (Income-Decimal $receipt.week_wallet_candidate_usdt) -eq $after.wallet) 'TRADES_SAVED_WALLET_INVALID'
   $selected+=,[pscustomobject]@{receipt_sha256=$receiptFile.sha256;window_sha256=$windowFile.sha256;week_start_ms=$start;old_cutoff_ms=$cutoff;day_boundary_ms=$bounds.day_ms;day_key=$bounds.day_key;week_key=$bounds.week_key;wallet=(Income-Text $after.wallet);saved_income_rows=0;saved_positions_flat=$true}
  }
  Inspect-Require ($selected.Count -eq 1) 'TRADES_SAVED_MATCH_NOT_UNIQUE'
  $source=$selected[0];$source|Add-Member NoteProperty streams $streams;Trades-VerifySaved $source;return $source
 } catch {foreach($entry in $streams){$entry.stream.Dispose()};throw}
}
