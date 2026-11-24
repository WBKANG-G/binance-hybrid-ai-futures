param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$CheckOnly,[switch]$Prepare)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$streams=New-Object Collections.ArrayList
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256){throw 'PLAN_TRANSFER_INVALID'}
 $m=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 $required=@('__FILES__')
 if((@($m.files.PSObject.Properties.Name|Sort-Object)-join '|') -cne (@($required|Sort-Object)-join '|')){throw 'PLAN_TRANSFER_INVALID'}
 foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $p.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $p.Value){throw 'PLAN_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 foreach($n in @('READ_CORE.ps1','PRIVATE_STORAGE.ps1','PLAN_CORE.ps1')){. (Join-Path $PSScriptRoot $n)}
 Plan-Require (([int][bool]$CheckOnly+[int][bool]$Prepare) -eq 1) 'PLAN_EXPLICIT_MODE_REQUIRED'
 $base=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
 $e=Get-OriginalPlanEvidence $base $streams
 if($CheckOnly){Write-Output 'WRITE_PLAN_INPUTS_CHECKED_NO_DB_START_NO_WRITES';exit 0}
 $plan=New-UnapprovedWritePlan $e $streams
 $dir=Reference-NewDirectory ([Guid]::NewGuid().ToString('N'))
 $out=Join-Path $dir 'UNAPPROVED_WRITE_PLAN.json'
 $bytes=[Text.Encoding]::UTF8.GetBytes(($plan|ConvertTo-Json -Depth 16 -Compress)+"`n")
 $f=New-Object IO.FileStream($out,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$f.Write($bytes,0,$bytes.Length);$f.Flush($true)}finally{$f.Dispose()}
 Plan-Require ((Get-FileHash -LiteralPath $out -Algorithm SHA256).Hash.ToLowerInvariant() -ceq (Inspect-Hash $bytes)) 'PLAN_OUTPUT_READBACK_FAILED'
 Plan-Recheck $streams
 [pscustomobject]@{write_plan='UNAPPROVED_PLAN_CREATED_NO_DATABASE_OR_DOCKER_ACCESS';private_plan_file=$out;plan_file_sha256=(Inspect-Hash $bytes);operation_id=$plan.operation_id;expected_fence=110;proposed_fence=111;proposed_fields=$plan.proposed_fields;owner_and_lease_action=$plan.owner_and_lease_action;source_files_sha256=$plan.source_files_sha256;blockers=$plan.blockers;files_written=$true;original_files_written=$false;docker_used=$false;original_database_started=$false;original_write_approval_received=$false;original_apply_ready=$false;commit_payload_verified=$false;apply_token_created=$false;baseline_adopted=$false;actual_http_attempts=0;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Depth 6 -Compress|Write-Output
 Write-Output 'UNAPPROVED_WRITE_PLAN_READY_NO_ORIGINAL_START_NO_APPLY_NO_ORDERS'
 exit 0
}catch{
 $code='PLAN_PREPARATION_BLOCKED';if($_.Exception.Message -cmatch '^(PLAN|REFERENCE|INSPECT)_[A-Z0-9_]{1,100}$'){$code=$_.Exception.Message}
 [pscustomobject]@{write_plan='BLOCKED_NO_ORIGINAL_CHANGE';failure_category=$code;docker_used=$false;original_database_started=$false;original_apply_ready=$false;automatic_retry=$false;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
 exit 2
}finally{foreach($s in $streams){$s.stream.Dispose()}}
