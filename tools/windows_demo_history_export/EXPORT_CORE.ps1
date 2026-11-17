function Export-WriteJson([string]$Path,[object]$Value) {
 $bytes=[Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 20 -Compress)+"`n")
 Inspect-Require ($bytes.Length -le 1048576) 'EXPORT_FILE_LIMIT'
 $file=New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try {$file.Write($bytes,0,$bytes.Length);$file.Flush($true)} finally {$file.Dispose()}
 Inspect-Require ((Inspect-Hash ([IO.File]::ReadAllBytes($Path))) -ceq (Inspect-Hash $bytes)) 'EXPORT_FILE_READBACK_FAILED'
}
function Export-Credentials {
 $values=@()
 foreach($label in @('DEMO API Key (hidden; local only)','DEMO API Secret (hidden; local only)')) {
  $secure=Read-Host $label -AsSecureString;$pointer=[IntPtr]::Zero
  try {$pointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure);$value=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer);Inspect-Require ($value -cmatch '^[!-~]{8,256}$') 'EXPORT_CREDENTIAL_INPUT_INVALID';$values+=,$value}
  finally {if($pointer -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)};$secure.Dispose()}
 }
 return [pscustomobject]@{key=$values[0];secret=$values[1]}
}
function Export-Query([hashtable]$Params) {
 return ((@($Params.Keys|Sort-Object)|ForEach-Object {[Uri]::EscapeDataString([string]$_)+'='+[Uri]::EscapeDataString([string]$Params[$_])}) -join '&')
}
function Export-SignedQuery([object]$Context,[hashtable]$Params) {
 Inspect-Require ($Context.watch.ElapsedMilliseconds -lt 60000 -and $null -ne $Context.server_time) 'EXPORT_CLOCK_OR_SESSION_EXPIRED'
 $copy=@{};foreach($name in $Params.Keys){$copy[$name]=$Params[$name]}
 $copy['timestamp']=([long]$Context.server_time+[long]$Context.watch.ElapsedMilliseconds-[long]$Context.clock_observed_ms).ToString([Globalization.CultureInfo]::InvariantCulture)
 $copy['recvWindow']='5000';$query=Export-Query $copy
 $bytes=[Text.Encoding]::UTF8.GetBytes($Context.credentials.secret);$hmac=New-Object Security.Cryptography.HMACSHA256
 try {$hmac.Key=$bytes;$signature=([BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($query)))).Replace('-','').ToLowerInvariant()}
 finally {$hmac.Dispose();[Array]::Clear($bytes,0,$bytes.Length)}
 return $query+'&signature='+$signature
}
function Export-ValidateRoute([string]$Path,[hashtable]$Params) {
 $names=(@($Params.Keys|Sort-Object) -join '|')
 $valid=$false
 switch -CaseSensitive ($Path) {
  '/fapi/v1/time' {$valid=$names -ceq ''}
  '/fapi/v1/exchangeInfo' {$valid=$names -ceq ''}
  '/fapi/v1/order' {$valid=$names -ceq 'orderId|symbol' -and [string]$Params.symbol -ceq 'BTCUSDT' -and [string]$Params.orderId -cmatch '^[1-9][0-9]{0,19}$'}
  '/fapi/v1/order/asyn' {$valid=$names -ceq 'endTime|startTime' -and [string]$Params.startTime -ceq '1792422000000' -and [string]$Params.endTime -cmatch '^[0-9]{13}$' -and [long]$Params.endTime -gt [long]$Params.startTime -and ([long]$Params.endTime-[long]$Params.startTime) -le 45L*86400000L}
  '/fapi/v1/order/asyn/id' {$valid=$names -ceq 'downloadId' -and [string]$Params.downloadId -cmatch '^[A-Za-z0-9_-]{1,128}$'}
 }
 Inspect-Require $valid 'EXPORT_ROUTE_OR_PARAMETERS_FORBIDDEN'
}
function Export-ReadBody([IO.Stream]$Stream,[int]$Limit) {
 $memory=New-Object IO.MemoryStream;$buffer=New-Object byte[] 65536
 try {while(($read=$Stream.Read($buffer,0,$buffer.Length)) -gt 0){Inspect-Require ($memory.Length+$read -le $Limit) 'EXPORT_RESPONSE_TOO_LARGE';$memory.Write($buffer,0,$read)};return [Text.Encoding]::UTF8.GetString($memory.ToArray())} finally {$memory.Dispose()}
}
function Export-NewWebRequest([string]$Uri) {
 [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
 return [Net.HttpWebRequest]::Create($Uri)
}
function Export-Http([object]$Context,[string]$Path,[hashtable]$Params) {
 Export-ValidateRoute $Path $Params
 Inspect-Require ($Context.watch.ElapsedMilliseconds -lt 60000 -and $Context.http_attempts -lt 5) 'EXPORT_REQUEST_BUDGET_EXCEEDED'
 $public=$Path -ceq '/fapi/v1/time' -or $Path -ceq '/fapi/v1/exchangeInfo'
 $query=Export-Query $Params;if(-not $public){$query=Export-SignedQuery $Context $Params}
 $uri='https://demo-fapi.binance.com'+$Path;if($query.Length -gt 0){$uri+='?'+$query}
 $request=Export-NewWebRequest $uri;$request.Method='GET';$request.AllowAutoRedirect=$false;$request.KeepAlive=$false;$request.Timeout=10000;$request.ReadWriteTimeout=10000
 if(-not $public){$request.Headers.Add('X-MBX-APIKEY',$Context.credentials.key)}
 $response=$null;$Context.http_attempts++
 try {
  try {$response=$request.GetResponse()}
  catch [Net.WebException] {
   $errorResponse=$_.Exception.Response
   if($null -ne $errorResponse) {try {$code=[int]$errorResponse.StatusCode;if($code -ge 300 -and $code -le 599){throw ('EXPORT_HTTP_'+$code)}} finally {$errorResponse.Dispose()}}
   throw 'EXPORT_TRANSPORT_OUTCOME_UNKNOWN'
  }
  Inspect-Require ([int]$response.StatusCode -eq 200) 'EXPORT_HTTP_STATUS_REJECTED'
  $body=Export-ReadBody $response.GetResponseStream() 4194304
  if(-not $public){Inspect-Require (-not $body.Contains($Context.credentials.key) -and -not $body.Contains($Context.credentials.secret)) 'EXPORT_RESPONSE_CONTAINS_CREDENTIAL_VALUE'}
  $weight=$response.Headers['X-MBX-USED-WEIGHT-1M']
  if($null -ne $weight){Inspect-Require ($weight -cmatch '^[0-9]{1,9}$') 'EXPORT_RATE_HEADER_INVALID';$Context.max_used_weight=[Math]::Max($Context.max_used_weight,[long]$weight);$Context.weight_observed=$true}
  return [pscustomobject]@{body=$body;weight_observed=($null -ne $weight);status=200}
 } finally {if($null -ne $response){$response.Dispose()};$request.Abort();$query=$null;$uri=$null}
}
function Export-Decode([string]$Body) {
 try {$value=$Body|ConvertFrom-Json} catch {throw 'EXPORT_RESPONSE_JSON_INVALID'}
 Inspect-Require ($value -is [pscustomobject]) 'EXPORT_RESPONSE_JSON_INVALID'
 return $value
}
function Export-Anchor([object]$Row,[object]$Data) {
 Inspect-Require ($Data.symbol -ceq 'BTCUSDT' -and [string]$Data.orderId -ceq [string]$Row.server_id -and $Data.clientOrderId -is [string] -and (Inspect-Hash ([Text.Encoding]::UTF8.GetBytes($Data.clientOrderId))) -ceq $Row.request_reference_sha256 -and $Data.status -ceq 'FILLED' -and $Data.side -ceq 'BUY' -and $Data.type -ceq 'LIMIT' -and [string]$Data.positionSide -ceq 'BOTH' -and [string]$Data.reduceOnly -ieq 'False') 'EXPORT_ACCOUNT_ANCHOR_MISMATCH'
 foreach($pair in @(@('origQty','quantity'),@('executedQty','quantity'),@('price','entry_limit_price'))) {
  $text=[string]$Data.($pair[0]);$expected=[string]$Row.($pair[1]);Inspect-Require ($text -cmatch '^[0-9]+(\.[0-9]+)?$' -and [decimal]::Parse($text,[Globalization.CultureInfo]::InvariantCulture) -eq [decimal]::Parse($expected,[Globalization.CultureInfo]::InvariantCulture)) 'EXPORT_ACCOUNT_ANCHOR_MISMATCH'
 }
}
function Export-Clock([object]$Context) {
 $before=$Context.watch.ElapsedMilliseconds;$reply=Export-Http $Context '/fapi/v1/time' @{};$data=Export-Decode $reply.body
 Inspect-Require ($Context.watch.ElapsedMilliseconds-$before -le 2000 -and [string]$data.serverTime -cmatch '^[0-9]{13}$') 'EXPORT_SERVER_CLOCK_INVALID'
 $Context.server_time=[long]$data.serverTime;$Context.clock_observed_ms=$Context.watch.ElapsedMilliseconds
 Inspect-Require ($Context.server_time -ge 1794870000000L -and $Context.server_time -lt 1796310000000L) 'EXPORT_PLAN_DATE_OUT_OF_RANGE'
}
function Export-RateLimit([object]$Context) {
 $reply=Export-Http $Context '/fapi/v1/exchangeInfo' @{};$data=Export-Decode $reply.body
 $limits=@($data.rateLimits|Where-Object {$_.rateLimitType -ceq 'REQUEST_WEIGHT' -and $_.interval -ceq 'MINUTE' -and $_.intervalNum -eq 1})
 Inspect-Require ($limits.Count -eq 1 -and [string]$limits[0].limit -cmatch '^[0-9]{1,5}$' -and [long]$limits[0].limit -ge 1200) 'EXPORT_RATE_LIMIT_UNVERIFIED'
 $Context.weight_limit=[Math]::Min(6000,[long]$limits[0].limit)
}
function Invoke-HistoryExport([object]$Plan,[string]$Mode) {
 $context=[pscustomobject]@{credentials=$null;watch=[Diagnostics.Stopwatch]::StartNew();server_time=$null;clock_observed_ms=0L;http_attempts=0;max_used_weight=0L;weight_observed=$false;weight_limit=0L}
 $directory=$null;$script:ReferenceAttemptPath=$null;$creationAttempted=$false;$jobRecorded=$false;$failure=$null
 try {
  Inspect-Require (@('RequestOnce','StatusOnce') -ccontains $Mode) 'EXPORT_EXPLICIT_MODE_REQUIRED'
  Inspect-Require (($null -eq $env:LIVE_TRADING_ENABLED -or $env:LIVE_TRADING_ENABLED -ieq 'false') -and $env:TRADING_MODE -ine 'LIVE') 'EXPORT_LIVE_CONTEXT_FORBIDDEN'
  foreach($name in @('BINANCE_DEMO_API_KEY','BINANCE_DEMO_API_SECRET','BINANCE_API_KEY','BINANCE_API_SECRET','BINANCE_LIVE_API_KEY','BINANCE_LIVE_API_SECRET')){Inspect-Require ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name))) 'EXPORT_ENV_CREDENTIALS_FORBIDDEN'}
  Inspect-Require (@($Plan.anchors).Count -eq 2 -and @($Plan.anchors.server_id|Select-Object -Unique).Count -eq 2) 'EXPORT_ANCHORS_INVALID'
  Inspect-Require ($Plan.policy -ceq 'DEMO_ORDER_EXPORT_20261117_V1' -and $Plan.host -ceq 'https://demo-fapi.binance.com' -and $Plan.export_path -ceq '/fapi/v1/order/asyn' -and $Plan.start_ms -eq 1792422000000L) 'EXPORT_PLAN_INVALID'
  $base=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData);$parent=Join-Path $base 'WBKANG-DEMO-Exports';$guard=Join-Path $parent 'ORDER_EXPORT_ATTEMPT_20261117.json'
  if($Mode -ceq 'RequestOnce') {
   Inspect-Require (-not [IO.File]::Exists($guard)) 'EXPORT_PRIOR_ATTEMPT_REVIEW_REQUIRED'
   $directory=Reference-NewDirectory ([Guid]::NewGuid().ToString('N'))
  } else {
   Reference-AssertPrivateDirectory $parent
   $stream=Inspect-Open $guard 16384
   try {$reader=New-Object IO.StreamReader($stream);try {$prior=Export-Decode $reader.ReadToEnd()} finally {$reader.Dispose()}} finally {$stream.Dispose()}
   Inspect-Require ($prior.policy -ceq 'DEMO_ORDER_EXPORT_INTENT_V1' -and $prior.manifest_sha256 -ceq $Plan.manifest_sha256 -and $prior.directory_token -cmatch '^[0-9a-f]{32}$') 'EXPORT_PRIOR_ATTEMPT_INVALID'
   $directory=Join-Path $parent ('history-export-20261117-'+$prior.directory_token);Reference-AssertPrivateDirectory $directory
   $jobFile=Join-Path $directory 'JOB.json';$stream=Inspect-Open $jobFile 16384
   try {$reader=New-Object IO.StreamReader($stream);try {$job=Export-Decode $reader.ReadToEnd()} finally {$reader.Dispose()}} finally {$stream.Dispose()}
   Inspect-Require ($job.policy -ceq 'DEMO_ORDER_EXPORT_JOB_V1' -and $job.manifest_sha256 -ceq $Plan.manifest_sha256 -and $job.key_fingerprint_sha256 -ceq $prior.key_fingerprint_sha256 -and $job.download_id -is [string] -and $job.download_id -cmatch '^[A-Za-z0-9_-]{1,128}$') 'EXPORT_JOB_INVALID'
  }
  $context.credentials=Export-Credentials
  $keyPin=Inspect-Hash ([Text.Encoding]::UTF8.GetBytes($context.credentials.key))
  if($Mode -ceq 'StatusOnce'){Inspect-Require ($keyPin -ceq $job.key_fingerprint_sha256) 'EXPORT_JOB_KEY_MISMATCH'}
  $context.watch.Restart()
  Export-Clock $context
  if($Mode -ceq 'RequestOnce') {
   Export-RateLimit $context
   foreach($anchor in $Plan.anchors) {
    $reply=Export-Http $context '/fapi/v1/order' @{symbol='BTCUSDT';orderId=[string]$anchor.server_id}
    $data=Export-Decode $reply.body;Export-Anchor $anchor $data
    Export-WriteJson (Join-Path $directory ('ANCHOR_'+$anchor.request_reference_sha256+'.json')) $data
    Inspect-Require $reply.weight_observed 'EXPORT_CURRENT_RATE_HEADER_REQUIRED'
   }
   Inspect-Require ($context.weight_observed -and $context.max_used_weight+1200 -le $context.weight_limit) 'EXPORT_INSUFFICIENT_WEIGHT_HEADROOM'
   $end=[long]$context.server_time
   $token=(Split-Path $directory -Leaf).Substring('history-export-20261117-'.Length)
   $intent=[ordered]@{policy='DEMO_ORDER_EXPORT_INTENT_V1';directory_token=$token;manifest_sha256=$Plan.manifest_sha256;key_fingerprint_sha256=$keyPin;start_ms=$Plan.start_ms;end_ms=$end;automatic_retry=$false;contains_api_credentials=$false}
   Export-WriteJson $guard $intent
   $creationAttempted=$true
   $reply=Export-Http $context '/fapi/v1/order/asyn' @{startTime=[string]$Plan.start_ms;endTime=[string]$end}
   $data=Export-Decode $reply.body;$id=Inspect-Member $data 'downloadId'
   Inspect-Require ($id -is [string] -and $id -cmatch '^[A-Za-z0-9_-]{1,128}$') 'EXPORT_CREATION_OUTCOME_UNRESOLVED'
   Export-WriteJson (Join-Path $directory 'JOB.json') ([ordered]@{policy='DEMO_ORDER_EXPORT_JOB_V1';manifest_sha256=$Plan.manifest_sha256;key_fingerprint_sha256=$keyPin;download_id=$id;response=$data;source='SIGNED_DEMO_RESPONSE';automatic_retry=$false})
   $jobRecorded=$true;$state='ONE_EXPORT_JOB_RECORDED_NO_ARCHIVE_DOWNLOADED'
  } else {
   $reply=Export-Http $context '/fapi/v1/order/asyn/id' @{downloadId=$job.download_id};$data=Export-Decode $reply.body
   $reported=Inspect-Member $data 'downloadId';Inspect-Require ($reported -is [string] -and $reported -ceq $job.download_id) 'EXPORT_STATUS_ID_MISMATCH'
   Export-WriteJson (Join-Path $directory ('STATUS_'+[Guid]::NewGuid().ToString('N')+'.json')) $data
   $jobRecorded=$true;$state='ONE_EXISTING_JOB_STATUS_SAVED_NO_ARCHIVE_DOWNLOADED'
  }
 } catch {
  if($null -eq $directory -and $null -ne $script:ReferenceAttemptPath){$directory=$script:ReferenceAttemptPath}
  $failure='EXPORT_RUN_BLOCKED';if($_.Exception.Message -cmatch '^(EXPORT|INSPECT|REFERENCE)_[A-Z0-9_]{1,100}$'){$failure=$_.Exception.Message};$state='STOPPED_NO_AUTOMATIC_RETRY'
 } finally {
  if($null -ne $context.credentials){$context.credentials.key=$null;$context.credentials.secret=$null;$context.credentials=$null};$context.watch.Stop()
 }
 return [pscustomobject]@{history_export=$state;failure_category=$failure;private_directory=$directory;export_creation_attempted=$creationAttempted;job_id_recorded=$jobRecorded;actual_http_attempts=$context.http_attempts;automatic_retry=$false;credentials_saved=$false;raw_client_ids_displayed=$false;archive_downloaded=$false;history_completeness_verified=$false;remote_request_resolution_verified=$false;baseline_adopted=$false;source_files_written=$false;docker_used=$false;services_started=$false;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;external_ip_usage_fully_known=$false}
}
