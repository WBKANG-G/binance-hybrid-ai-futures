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
  '/fapi/v1/userTrades' {$valid=$names -ceq 'endTime|limit|startTime|symbol' -and [string]$Params.symbol -ceq 'BTCUSDT' -and [string]$Params.limit -ceq '1000' -and [string]$Params.startTime -cmatch '^[0-9]{13}$' -and [string]$Params.endTime -cmatch '^[0-9]{13}$' -and [long]$Params.startTime -ge 1794754800000L -and [long]$Params.endTime -lt 1796310000000L -and [long]$Params.endTime -ge [long]$Params.startTime -and ([long]$Params.endTime-[long]$Params.startTime) -le 604800000L}
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
 Inspect-Require ($Context.watch.ElapsedMilliseconds -lt 60000 -and $Context.http_attempts -lt 7) 'EXPORT_REQUEST_BUDGET_EXCEEDED'
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
