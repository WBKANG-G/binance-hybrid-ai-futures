# Managed native execution avoids PS5.1 NativeCommandError on stderr under Stop.
$script:ScopeDockerDiagnostic=$null
function Scope-NativeArgument([string]$Value) {
 if($Value.Length -gt 8192 -or $Value.Contains([string][char]0) -or $Value.Contains("`r") -or $Value.Contains("`n")){throw 'SCOPE_DOCKER_ARGUMENT_INVALID'}
 $escaped=[regex]::Replace($Value,'(\\*)"',{param($m) return (('\'*(2*$m.Groups[1].Value.Length+1))+'"')})
 $escaped=[regex]::Replace($escaped,'(\\+)$',{param($m) return ('\'*(2*$m.Groups[1].Value.Length))})
 return ('"'+$escaped+'"')
}
function Invoke-ScopeDockerRead([string[]]$Arguments,[int]$Maximum=128) {
 $op='FORBIDDEN'
 if($Arguments.Count -eq 4 -and ($Arguments -join '|') -ceq 'ps|--all|--quiet|--no-trunc'){$op='LIST_CONTAINERS'}
 elseif($Arguments.Count -eq 4 -and $Arguments[0] -ceq 'inspect' -and $Arguments[1] -ceq '--format' -and $Arguments[3] -cmatch '^[0-9a-f]{64}$'){$op='INSPECT_CONTAINER'}
 elseif($Arguments.Count -eq 5 -and $Arguments[0] -ceq 'image' -and $Arguments[1] -ceq 'inspect' -and $Arguments[2] -ceq '--format' -and $Arguments[3] -ceq '{{.Id}}' -and $Arguments[4] -cmatch '^sha256:[0-9a-f]{64}$'){$op='INSPECT_IMAGE'}
 elseif($Arguments.Count -eq 5 -and $Arguments[0] -ceq 'volume' -and $Arguments[1] -ceq 'inspect' -and $Arguments[2] -ceq '--format' -and $Arguments[4] -cmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$'){$op='INSPECT_VOLUME'}
 $script:ScopeDockerDiagnostic=[ordered]@{operation=$op;executable_found=$false;process_started=$false;exit_code=$null;stderr_present=$false;failure_code=$null}
 if($op -ceq 'FORBIDDEN' -or $Maximum -lt 1 -or $Maximum -gt 256){$script:ScopeDockerDiagnostic.failure_code='SCOPE_DOCKER_READ_OPERATION_FORBIDDEN';throw 'SCOPE_DOCKER_READ_OPERATION_FORBIDDEN'}
 $commands=@(Get-Command -Name docker -CommandType Application -ErrorAction SilentlyContinue)
 if($commands.Count -lt 1){$script:ScopeDockerDiagnostic.failure_code='SCOPE_DOCKER_CLI_NOT_FOUND';throw 'SCOPE_DOCKER_CLI_NOT_FOUND'}
 $script:ScopeDockerDiagnostic.executable_found=$true
 $info=New-Object Diagnostics.ProcessStartInfo
 $info.FileName=$commands[0].Source
 $info.Arguments=(@($Arguments|ForEach-Object {Scope-NativeArgument $_}) -join ' ')
 $info.UseShellExecute=$false;$info.CreateNoWindow=$true
 $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
 $info.StandardOutputEncoding=New-Object Text.UTF8Encoding($false)
 $info.StandardErrorEncoding=New-Object Text.UTF8Encoding($false)
 $process=New-Object Diagnostics.Process;$process.StartInfo=$info
 try {
  try {$started=$process.Start()}catch{throw 'SCOPE_DOCKER_PROCESS_START_FAILED'}
  if(-not $started){throw 'SCOPE_DOCKER_PROCESS_START_FAILED'}
  $script:ScopeDockerDiagnostic.process_started=$true
  $stdout=$process.StandardOutput.ReadToEndAsync();$stderr=$process.StandardError.ReadToEndAsync()
  if(-not $process.WaitForExit(15000)){$script:ScopeDockerDiagnostic.failure_code='SCOPE_DOCKER_READ_TIMEOUT';try{if(-not $process.HasExited){$process.Kill()}}catch{};throw 'SCOPE_DOCKER_READ_TIMEOUT'}
  if(-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)){$script:ScopeDockerDiagnostic.failure_code='SCOPE_DOCKER_OUTPUT_TIMEOUT';throw 'SCOPE_DOCKER_OUTPUT_TIMEOUT'}
  $text=$stdout.Result;$errorText=$stderr.Result
  $script:ScopeDockerDiagnostic.exit_code=$process.ExitCode
  $script:ScopeDockerDiagnostic.stderr_present=($errorText.Length -gt 0)
  if($process.ExitCode -ne 0){
   $code='SCOPE_DOCKER_READ_FAILED'
   if($errorText -match '(?i)cannot connect|is the docker daemon running|error during connect|dockerDesktopLinuxEngine|docker_engine|system cannot find the file|connection refused'){$code='SCOPE_DOCKER_DAEMON_UNAVAILABLE'}
   $script:ScopeDockerDiagnostic.failure_code=$code;throw $code
  }
  if($text.Length -gt 8388608 -or $errorText.Length -gt 1048576){throw 'SCOPE_DOCKER_OUTPUT_LIMIT'}
  if($text.Length -eq 0){return @()}
  $lines=@($text -split '\r?\n')
  if($lines[-1] -ceq ''){$lines=@($lines|Select-Object -First ($lines.Count-1))}
  if($lines.Count -gt $Maximum){throw 'SCOPE_DOCKER_OUTPUT_LIMIT'}
  foreach($line in $lines){if($line.Length -gt 65536){throw 'SCOPE_DOCKER_OUTPUT_LIMIT'}}
  return $lines
 } catch {if($_.Exception.Message -cmatch '^SCOPE_DOCKER_[A-Z0-9_]{1,100}$'){$script:ScopeDockerDiagnostic.failure_code=$_.Exception.Message};throw} finally {$process.Dispose()}
}
