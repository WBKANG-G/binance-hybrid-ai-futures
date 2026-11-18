$script:TypedReview=$null
function Invoke-TypedDraftReview([string]$Root,[object]$Config,[object]$State,[string]$Socket,[object[]]$Original,[string[]]$Owned,[string]$Directory,[object]$Account) {
 $ids=@(Backup-Docker @('create','--pull=never','--interactive','--label','com.docker.compose.project=demo-copy-review','--label','com.docker.compose.service=typed-draft-review','--network','none','--restart','no','--read-only','--memory','768m','--pids-limit','32','--cap-drop','ALL','--security-opt','no-new-privileges','--user','10001:10001','--mount',('type=volume,source='+$Socket+',target=/review-socket,readonly,volume-nocopy'),'--entrypoint','python',$State.canonical_images[0],'-I','-B','-') 1)
 if($ids.Count -ne 1 -or $ids[0] -cnotmatch '^[0-9a-f]{64}$' -or @($Original.id) -ccontains $ids[0]){throw 'MODEL_HELPER_ID_INVALID'}
 $id=$ids[0];$script:CopyDiagnosticId=$id
 Write-BackupJson (Join-Path $Directory 'TYPED_DRAFT_RESOURCE.json') @{helper_id=$id;automatic_retry=$false}
 $item=Read-Container $id
 if((Project $item) -cne 'demo-copy-review' -or $item.running -or $item.restart.Name -cne 'no' -or @($item.mounts).Count -ne 1 -or -not (Resolve-RestoreImageMatch $item.image $State.canonical_images[0])){throw 'MODEL_HELPER_SCOPE_INVALID'}
 $mount=$item.mounts[0]
 if($mount.Type -cne 'volume' -or $mount.Name -cne $Socket -or $mount.Destination -cne '/review-socket' -or $mount.RW){throw 'MODEL_HELPER_SCOPE_INVALID'}
 $hostLines=@(Backup-Docker @('inspect','--format','{{json .HostConfig}}{{println}}{{json .Config.User}}{{println}}{{json .Config.Entrypoint}}{{println}}{{json .Config.Cmd}}',$id) 4)
 if($hostLines.Count -ne 4){throw 'MODEL_HELPER_SCOPE_INVALID'}
 $h=ConvertFrom-Json -InputObject $hostLines[0]
 $u=ConvertFrom-Json -InputObject ('{"value":'+$hostLines[1]+'}');$e=ConvertFrom-Json -InputObject ('{"value":'+$hostLines[2]+'}');$c=ConvertFrom-Json -InputObject ('{"value":'+$hostLines[3]+'}')
 if($h.NetworkMode -cne 'none' -or -not $h.ReadonlyRootfs -or $h.Privileged -or $h.Memory -ne 805306368 -or $h.PidsLimit -ne 32 -or (@($h.CapDrop) -join '|') -cne 'ALL' -or @($h.CapAdd|Where-Object {$null -ne $_}).Count -ne 0 -or (@($h.SecurityOpt) -join '|') -cne 'no-new-privileges' -or $u.value -cne '10001:10001' -or (@($e.value) -join '|') -cne 'python' -or (@($c.value) -join '|') -cne '-I|-B|-'){throw 'MODEL_HELPER_ISOLATION_INVALID'}
 Assert-RestoreSourceSnapshot $Original @($Owned+@($id)) $false
 $d=$Account.transition_draft
 $expected=[ordered]@{review_id=$d.review_id;before_state_sha256=$d.before_state_sha256;candidate_draft_state_sha256=$d.candidate_draft_state_sha256;evidence_sha256=$d.evidence_sha256;expected_fence=$d.expected_fence;proposed_fence=$d.proposed_fence;proposed_fields=$d.audit_draft.proposed_fields}
 $params=[ordered]@{draft_day=$script:DraftEvidence.day;draft_week=$script:DraftEvidence.week;draft_amount=$script:DraftEvidence.amount;draft_evidence=$script:DraftEvidence.sha256}
 $cfg=[ordered]@{baseline_files=$Config.baseline_files;candidate_sha256=$Config.candidate_sha256;sql_sha256=$Config.sql_sha256;params=$params;expected=$expected}
 $cfg64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($cfg|ConvertTo-Json -Depth 10 -Compress)))
 $module=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'model_review.py')))
 $sql=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'account_check.sql')))
 $worker=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'typed_worker.py')))
 $payload="import base64,json`nCONFIG=json.loads(base64.b64decode('$cfg64'))`nMODULE_BODY=base64.b64decode('$module')`nSQL_BODY=base64.b64decode('$sql')`nexec(compile(base64.b64decode('$worker'),'<typed-draft-worker>','exec'))"
 $lines=@($payload|& docker start --attach --interactive $id 2>$null);$code=$LASTEXITCODE
 if($lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 16384){throw 'MODEL_HELPER_OUTPUT_INVALID'}
 $value=$lines[0]|ConvertFrom-Json
 if($code -ne 0){if($value.failure_category -cmatch '^MODEL_[A-Z0-9_]{1,100}$'){$script:CopyDiagnosticFailure=$value.failure_category;if($value.failure_category -ceq 'MODEL_WORKER_BLOCKED' -and @('SOURCE_CHECK','MODEL_IMPORT','SQL_CONNECT','SQL_PRECHECK','SQL_READ','MODEL_VALIDATE') -ccontains $value.failure_stage -and $value.exception_type -cmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$'){$script:CopyDiagnosticFailure='MODEL_'+$value.failure_stage+'_'+$value.exception_type.ToUpperInvariant()}};throw 'MODEL_REVIEW_BLOCKED'}
 if($value.typed_draft_review -cne 'TYPED_LEDGER_AND_UNAPPROVED_AUDIT_VALIDATED_NO_APPLY' -or $value.review_id -cne $d.review_id -or $value.before_state_sha256 -cne $d.before_state_sha256 -or $value.candidate_draft_state_sha256 -cne $d.candidate_draft_state_sha256 -or $value.evidence_sha256 -cne $d.evidence_sha256 -or $value.expected_fence -ne $d.expected_fence -or $value.proposed_fence -ne $d.proposed_fence -or $value.baseline_files_verified -ne 89){throw 'MODEL_REVIEW_OUTPUT_INVALID'}
 foreach($flag in @('before_model_validated','candidate_model_validated','audit_model_validated','completed_adoption_schema_rejected_draft','protected_state_preserved','copy_sql_read_only','repeated_read_equal')){if($value.$flag -isnot [bool] -or -not $value.$flag){throw 'MODEL_REVIEW_OUTPUT_INVALID'}}
 foreach($flag in @('apply_token_created','commit_payload_verified','source_accepted','database_changed','audit_record_written','baseline_adopted','request_states_changed','execution_approved','orders_enabled','live_trading_enabled')){if($value.$flag -isnot [bool] -or $value.$flag){throw 'MODEL_REVIEW_OUTPUT_INVALID'}}
 $exit=@(Backup-Docker @('inspect','--format','{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}}',$id) 1)
 if($exit.Count -ne 1 -or $exit[0] -cne 'false 0 false'){throw 'MODEL_HELPER_NOT_STOPPED'}
 Assert-RestoreSourceSnapshot $Original @($Owned+@($id)) $false
 $script:TypedReview=$value
 Write-BackupJson (Join-Path $Directory 'TYPED_DRAFT_REVIEW.json') $value
 return $id
}
