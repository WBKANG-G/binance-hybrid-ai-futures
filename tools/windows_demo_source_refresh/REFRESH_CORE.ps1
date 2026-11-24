# Existing receipt-bound reader only; all original services remain stopped.
$script:RefreshGuardedIds=(Get-Item Function:Container-Ids).ScriptBlock
$script:RefreshHelperVisible=$false
$script:RefreshStartAttempted=$false
function Container-Ids {
 if($script:RefreshHelperVisible){return @(Financial-AllContainerIds)}
 return @(& $script:RefreshGuardedIds)
}
function Assert-RefreshReader([object]$Config,[object]$State,[object[]]$Original) {
 $id=$Config.comparison_helper_id
 Assert-CompareHelper $id $State $Original
 $lines=@(Backup-Docker @('inspect','--format','{{json .Config.OpenStdin}}{{println}}{{json .Config.StdinOnce}}{{println}}{{json .Config.Tty}}',$id) 3)
 if($lines.Count -ne 3 -or $lines[0] -cne 'true' -or $lines[1] -cne 'true' -or $lines[2] -cne 'false'){throw 'COMPARE_EXISTING_READER_STDIN_INVALID'}
 $exit=@(Backup-Docker @('inspect','--format','{{.State.ExitCode}} {{.State.OOMKilled}}',$id) 1)
 if($exit.Count -ne 1 -or $exit[0] -cne '0 false'){throw 'COMPARE_EXISTING_READER_EXIT_INVALID'}
}
function Invoke-RefreshPayload([object]$Config,[object]$State,[object[]]$Original,[string]$Payload) {
 Assert-RefreshReader $Config $State $Original
 if($Payload -cmatch '[^\x00-\x7f]' -or [Text.Encoding]::UTF8.GetByteCount($Payload) -gt 33554432){throw 'COMPARE_PAYLOAD_LIMIT'}
 $id=$Config.comparison_helper_id
 $commands=@(Get-Command docker -CommandType Application -ErrorAction SilentlyContinue)
 if($commands.Count -lt 1){throw 'SCOPE_DOCKER_CLI_NOT_FOUND'}
 $info=New-Object Diagnostics.ProcessStartInfo
 $info.FileName=$commands[0].Source
 $info.Arguments=(@(@('start','--attach','--interactive',$id)|ForEach-Object {Scope-NativeArgument $_}) -join ' ')
 $info.UseShellExecute=$false;$info.CreateNoWindow=$true
 $info.RedirectStandardInput=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
 $info.StandardOutputEncoding=New-Object Text.UTF8Encoding($false)
 $info.StandardErrorEncoding=New-Object Text.UTF8Encoding($false)
 $process=New-Object Diagnostics.Process;$process.StartInfo=$info
 try {
  $script:RefreshStartAttempted=$true
  try{$started=$process.Start()}catch{throw 'COMPARE_READER_CLIENT_START_FAILED'}
  if(-not $started){throw 'COMPARE_READER_CLIENT_START_FAILED'}
  $out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync()
  $inputTask=$process.StandardInput.WriteAsync($Payload)
  if(-not $inputTask.Wait(10000)){throw 'COMPARE_READER_INPUT_TIMEOUT'}
  $process.StandardInput.Close()
  if(-not $process.WaitForExit(150000)){throw 'COMPARE_READER_CLIENT_TIMEOUT'}
  if(-not $out.Wait(2000) -or -not $err.Wait(2000)){throw 'COMPARE_READER_OUTPUT_TIMEOUT'}
  $stdout=$out.Result
  if($process.ExitCode -ne 0){throw 'COMPARE_READER_RUN_FAILED'}
  if($stdout.Length -gt 4096 -or $err.Result.Length -gt 1048576){throw 'COMPARE_READER_OUTPUT_LIMIT'}
  $lines=@($stdout -split '\r?\n');if($lines.Count -gt 0 -and $lines[-1] -ceq ''){$lines=@($lines|Select-Object -First ($lines.Count-1))}
  if($lines.Count -ne 1){throw 'COMPARE_READER_OUTPUT_INVALID'}
  try{$value=ConvertFrom-Json -InputObject ([string]$lines[0])}catch{throw 'COMPARE_READER_OUTPUT_INVALID'}
  return $value
 } finally {
  # Only terminate this newly started Docker CLI client, never a container/service.
  try{if($script:RefreshStartAttempted -and -not $process.HasExited){$process.Kill()}}catch{}
  $process.Dispose()
 }
}
function Assert-RefreshResult([object]$Value,[object]$Manifest) {
 if($Value.status -cne 'STOPPED_SOURCE_TREES_MATCH_PINNED_BACKUP' -or $Value.failure_category -cne 'NONE' -or $Value.passes -ne 2 -or $Value.source_data_written -isnot [bool] -or $Value.source_data_written -or $Value.logical_account_row_queried -isnot [bool] -or $Value.logical_account_row_queried){throw 'COMPARE_READER_NOT_MATCHED'}
 foreach($g in @('database','runtime')){
  if($Value.file_counts.$g -ne @($Manifest.groups.$g.entries|Where-Object {$_.kind -ceq 'file'}).Count -or $Value.logical_bytes.$g -ne $Manifest.groups.$g.logical_bytes){throw 'COMPARE_READER_COUNTS_INVALID'}
 }
}
