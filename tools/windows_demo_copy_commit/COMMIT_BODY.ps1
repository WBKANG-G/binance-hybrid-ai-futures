 $d=$Account.transition_draft
 $expected=[ordered]@{review_id=$d.review_id;before_state_sha256=$d.before_state_sha256;candidate_draft_state_sha256=$d.candidate_draft_state_sha256;candidate_financial_state_sha256=$d.candidate_financial_state_sha256;evidence_sha256=$d.evidence_sha256;expected_fence=$d.expected_fence;proposed_fence=$d.proposed_fence;proposed_fields=$d.audit_draft.proposed_fields}
 $params=[ordered]@{draft_day=$script:DraftEvidence.day;draft_week=$script:DraftEvidence.week;draft_amount=$script:DraftEvidence.amount;draft_evidence=$script:DraftEvidence.sha256}
 $cfg=[ordered]@{baseline_files=$Config.baseline_files;candidate_sha256=$Config.commit_candidate_sha256;sql_sha256=$Config.sql_sha256;params=$params;expected=$expected;read_worker_sha256=$Config.read_worker_sha256}
 $cfg64=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($cfg|ConvertTo-Json -Depth 10 -Compress)))
 $module=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'commit_model.py')))
 $sql=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'account_check.sql')))
 $read=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'typed_worker.py')))
 $worker=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $Root 'copy_commit_worker.py')))
 $payload="import base64,json`nCONFIG=json.loads(base64.b64decode('$cfg64'))`nMODULE_BODY=base64.b64decode('$module')`nSQL_BODY=base64.b64decode('$sql')`nREAD_WORKER=base64.b64decode('$read')`nexec(compile(base64.b64decode('$worker'),'<copy-commit-worker>','exec'))"
 $lines=@($payload|& docker start --attach --interactive $id 2>$null);$code=$LASTEXITCODE
 if($lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 16384){throw 'MODEL_COPY_OUTPUT_INVALID'}
 $value=$lines[0]|ConvertFrom-Json
 if($code -ne 0){if($value.failure_category -cmatch '^MODEL_[A-Z0-9_]{1,100}$'){$script:CopyDiagnosticFailure=$value.failure_category};throw 'MODEL_COPY_TRANSACTION_BLOCKED'}
 if($value.context -cne 'COPY_TRANSACTION_REHEARSAL_ONLY' -or $value.completion_record_context -cne 'COPY_REHEARSAL_ONLY' -or $value.rollback_cases_verified -ne 3 -or $value.events_added -ne 1 -or $value.outbox_added -ne 1 -or $value.before_state_sha256 -cne $d.before_state_sha256 -or $value.copy_after_state_sha256 -cnotmatch '^[0-9a-f]{64}$' -or $value.source_fence -ne $d.expected_fence -or $value.copy_fence -ne $d.proposed_fence -or $value.copy_day -cne $d.candidate_day -or $value.copy_week -cne $d.candidate_week){throw 'MODEL_COPY_OUTPUT_INVALID'}
 foreach($flag in @('copy_commit_verified','copy_event_outbox_atomicity_verified','copy_idempotency_verified','independent_postcommit_read_verified','protected_raw_json_preserved','real_application_rejected')){if($value.$flag -isnot [bool] -or -not $value.$flag){throw 'MODEL_COPY_OUTPUT_INVALID'}}
 foreach($flag in @('original_source_accepted','original_database_changed','original_baseline_adopted','request_states_changed','execution_approved','orders_enabled','live_trading_enabled')){if($value.$flag -isnot [bool] -or $value.$flag){throw 'MODEL_COPY_OUTPUT_INVALID'}}
 if($value.actual_http_attempts -ne 0 -or $value.order_writes -ne 0){throw 'MODEL_COPY_OUTPUT_INVALID'}
 $exit=@(Backup-Docker @('inspect','--format','{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}}',$id) 1)
 if($exit.Count -ne 1 -or $exit[0] -cne 'false 0 false'){throw 'MODEL_COPY_HELPER_NOT_STOPPED'}
 Assert-NewClone $Clone $CloneVolume $Original
 Assert-RestoreSourceSnapshot $Original @($Owned+@($id)) $false
 $script:CopyCommitReview=$value
 Write-BackupJson (Join-Path $Directory 'COPY_COMMIT_REVIEW.json') $value
 return $id
}
