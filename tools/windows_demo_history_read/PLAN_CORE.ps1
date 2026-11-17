function Invoke-HistoryReadPlan([string]$Root,[string]$ManifestPin) {
 $streams=New-Object Collections.ArrayList
 try {
        Inspect-Require ($ManifestPin -cmatch '^[0-9a-f]{64}$' -and ([IO.File]::GetAttributes($Root) -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'INSPECT_ROOT_INVALID'
        $folders=@(Get-ChildItem -LiteralPath $Root -Directory -Filter 'cold-*');Inspect-Require ($folders.Count -le 32) 'INSPECT_SEARCH_LIMIT'
        $backups=@()
        foreach ($folder in $folders) {
            Inspect-Require ($folder.Name -cmatch '^cold-20261114-[0-9a-f]{32}$' -and ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) 'INSPECT_DIRECTORY_INVALID'
            $path=Join-Path $folder.FullName 'manifest.json'
            if (-not [IO.File]::Exists($path)) {continue}
            $stream=Inspect-Open $path 16777216;$null=$streams.Add($stream)
            if ((Inspect-StreamHash $stream) -ceq $ManifestPin) {$backups+=,[pscustomobject]@{folder=$folder.FullName;stream=$stream}}
        }
        Inspect-Require ($backups.Count -eq 1) 'INSPECT_BACKUP_NOT_UNIQUE'
        $source=$backups[0];$reader=New-Object IO.StreamReader($source.stream,(New-Object Text.UTF8Encoding($false,$true)),$true,1024,$true)
        try {$m=Inspect-Json $reader.ReadToEnd()} finally {$reader.Dispose()}
        $archive=Join-Path $source.folder 'runtime.tar';$runtime=Inspect-Open $archive 67108864;$null=$streams.Add($runtime)
        Inspect-Require ($runtime.Length -eq $m.groups.runtime.archive_bytes -and (Inspect-StreamHash $runtime) -ceq $m.groups.runtime.archive_sha256) 'INSPECT_ARCHIVE_HASH_MISMATCH'
        $adopt=Inspect-Record $m $archive '^demo-runtime-20261111/accounted-day13-adoption-[0-9a-f-]{36}/intent\.json$'
        $plan=Inspect-Record $m $archive '^demo-runtime-20261111/historical-request-evidence-[0-9a-f-]{36}/intent\.json$'
        Inspect-Require ($adopt.policy -ceq 'OPERATOR_ATTESTED_ACCOUNTED_DAY13_ADOPTION_V1' -and $adopt.old_row.account_id -ceq 'DEMO-WINDOWS-ISOLATED-BTC-V1' -and $plan.account -ceq $adopt.old_row.account_id) 'INSPECT_ACCOUNT_LINK_INVALID'
        $rows=@(Inspect-RequestRows $adopt.old_row.state $plan)
        $original=Inspect-Record $m $archive '^demo-runtime-20261111/historical-request-evidence-[0-9a-f-]{36}/complete\.json$'
  Inspect-Require ($original.policy -ceq 'HISTORICAL_ORIGINAL_ID_EVIDENCE_20261113_V1') 'EXPORT_REPORT_SCOPE_INVALID'
  $anchors=@($rows|Where-Object {$_.role -ceq 'ENTRY'})
  Inspect-Require ($anchors.Count -eq 2) 'EXPORT_ANCHORS_INVALID'
  foreach($anchor in $anchors) {
   $proof=@($original.observations|Where-Object {$_.role -ceq 'ENTRY' -and $_.request_reference_sha256 -ceq $anchor.request_reference_sha256})
   Inspect-Require ($null -ne $anchor.server_id -and $proof.Count -eq 1 -and $proof[0].observed_status -ceq 'FILLED' -and $proof[0].response_identity_fields_match -is [bool] -and $proof[0].response_identity_fields_match) 'EXPORT_ANCHORS_INVALID'
  }
  Inspect-Require (@($anchors.server_id|Select-Object -Unique).Count -eq 2) 'EXPORT_ANCHORS_INVALID'
  Inspect-Require ((Inspect-StreamHash $source.stream) -ceq $ManifestPin -and (Inspect-StreamHash $runtime) -ceq $m.groups.runtime.archive_sha256) 'INSPECT_SOURCE_CHANGED'
  return [pscustomobject]@{policy='DEMO_HISTORY_READ_20261117_V1';manifest_sha256=$ManifestPin;archive_sha256=$m.groups.runtime.archive_sha256;anchors=$anchors;targets=@($rows|Where-Object {$_.role -cin @('EXIT','STOP_LOSS')});start_ms=1792422000000;start_utc='2026-10-19T15:00:00Z';host='https://demo-fapi.binance.com';read_window_start_ms=1792454400000L;read_window_end_ms=1792540799999L;export_creation_allowed=$false;demo_support_verified=$false;request_resolution_authorized=$false;baseline_adoption_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
 } finally {foreach($stream in $streams){$stream.Dispose()}}
}
