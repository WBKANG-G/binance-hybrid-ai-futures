$script:OwnerFinancialValidator=(Get-Item Function:Assert-CopyFinancial).ScriptBlock
$script:OwnerProtocolReview=$null
function Owner-Require([bool]$Condition,[string]$Code) {if(-not $Condition){throw $Code}}
function Owner-Bool([object]$Value,[bool]$Expected) {return ($Value -is [bool] -and $Value -eq $Expected)}
function Assert-CopyFinancial([object]$Account) {
 Owner-Require ($null -ne $Account.PSObject.Properties['owner_review'] -and $null -ne $Account.PSObject.Properties['period_candidate']) 'OWNER_SQL_SCHEMA_INVALID'
 $o=$Account.owner_review;$p=$Account.period_candidate
 $financial=$Account|Select-Object -Property * -ExcludeProperty owner_review,period_candidate
 & $script:OwnerFinancialValidator $financial
 Owner-Require ((@($o.PSObject.Properties.Name|Sort-Object)-join '|') -ceq 'fence|lease_clock_scope|lease_present|lease_state|lease_until_utc|original_owner_verified|owner_present|owner_sha256|ownership_row_sha256|row_lock_acquired') 'OWNER_SQL_SCHEMA_INVALID'
 Owner-Require (($o.fence -is [int] -or $o.fence -is [long]) -and $o.fence -eq 110) 'OWNER_REFERENCE_FENCE_CHANGED'
 Owner-Require ($o.owner_present -is [bool] -and $o.lease_present -is [bool] -and $o.lease_clock_scope -ceq 'COPY_DATABASE_STATEMENT_TIMESTAMP' -and (Owner-Bool $o.original_owner_verified $false) -and (Owner-Bool $o.row_lock_acquired $false) -and $o.ownership_row_sha256 -cmatch '^[0-9a-f]{64}$') 'OWNER_SQL_SCOPE_INVALID'
 if($o.owner_present){Owner-Require ($o.owner_sha256 -is [string] -and $o.owner_sha256 -cmatch '^[0-9a-f]{64}$') 'OWNER_IDENTITY_HASH_INVALID'}else{Owner-Require ($null -eq $o.owner_sha256) 'OWNER_IDENTITY_HASH_INVALID'}
 if($o.lease_present){
  Owner-Require (@('ACTIVE','EXPIRED') -ccontains $o.lease_state) 'OWNER_LEASE_SCHEMA_INVALID'
  $stamp=$o.lease_until_utc
  if($stamp -is [DateTime]){Owner-Require ($stamp.Kind -eq [DateTimeKind]::Utc) 'OWNER_LEASE_SCHEMA_INVALID'}
  elseif($stamp -is [DateTimeOffset]){Owner-Require ($stamp.Offset -eq [TimeSpan]::Zero) 'OWNER_LEASE_SCHEMA_INVALID'}
  else{
   Owner-Require ($stamp -is [string] -and $stamp -cmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$') 'OWNER_LEASE_SCHEMA_INVALID'
   try{$null=[DateTimeOffset]::ParseExact($stamp,"yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'",[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::AssumeUniversal)}catch{throw 'OWNER_LEASE_SCHEMA_INVALID'}
  }
 }else{Owner-Require ($null -eq $o.lease_until_utc -and $o.lease_state -ceq 'ABSENT') 'OWNER_LEASE_SCHEMA_INVALID'}
 Owner-Require ((@($p.PSObject.Properties.Name|Sort-Object)-join '|') -ceq 'apply_token_created|audit_record_created|before_state_pg_text_sha256|candidate_financial_pg_text_sha256|candidate_paused|changed_top_level_fields|commit_payload_verified|context|orders_requests_operations_preserved|paused_preserved|proposed_fields|protected_jsonb_preserved') 'OWNER_CANDIDATE_SCHEMA_INVALID'
 Owner-Require ($p.context -ceq 'UNAPPROVED_ORIGINAL_RECOVERY_DESIGN_ONLY' -and $p.before_state_pg_text_sha256 -ceq $Account.account_state_sha256 -and $p.candidate_financial_pg_text_sha256 -cmatch '^[0-9a-f]{64}$' -and $p.candidate_financial_pg_text_sha256 -cne $Account.account_state_sha256) 'OWNER_CANDIDATE_HASH_INVALID'
 foreach($k in @('protected_jsonb_preserved','orders_requests_operations_preserved','paused_preserved','candidate_paused')){Owner-Require (Owner-Bool $p.$k $true) 'OWNER_PROTECTED_STATE_CHANGED'}
 foreach($k in @('audit_record_created','commit_payload_verified','apply_token_created')){Owner-Require (Owner-Bool $p.$k $false) 'OWNER_DRAFT_AUTHORITY_INVALID'}
 $fields=$p.proposed_fields
 Owner-Require ((@($fields.PSObject.Properties.Name|Sort-Object)-join '|') -ceq 'daily_base|daily_key|reconciled|stream_ready|weekly_base|weekly_key' -and $fields.daily_key -is [string] -and $fields.daily_key -ceq '2026-11-24' -and $fields.weekly_key -ceq '2026-11-23' -and $fields.daily_base -is [string] -and $fields.weekly_base -is [string] -and $fields.daily_base -ceq '4998.74183608' -and $fields.weekly_base -ceq '4998.74183608' -and (Owner-Bool $fields.reconciled $false) -and (Owner-Bool $fields.stream_ready $false)) 'OWNER_CANDIDATE_FIELDS_INVALID'
 $changes=@($p.changed_top_level_fields)
 Owner-Require (@($changes|Sort-Object -Unique).Count -eq $changes.Count -and $changes.Count -le 6 -and $changes -ccontains 'daily_key' -and $changes -ccontains 'weekly_key') 'OWNER_CHANGED_FIELDS_INVALID'
 foreach($k in $changes){Owner-Require (@('daily_key','weekly_key','daily_base','weekly_base','reconciled','stream_ready') -ccontains $k) 'OWNER_PROTECTED_STATE_CHANGED'}
 $classification='UNOWNED_NO_LEASE'
 if($o.lease_state -ceq 'ACTIVE'){$classification='ACTIVE_LEASE_BLOCKED'}
 elseif($o.owner_present -and -not $o.lease_present){$classification='OWNER_WITHOUT_LEASE_BLOCKED'}
 elseif($o.owner_present){$classification='EXPIRED_OWNER_REQUIRES_EXPLICIT_TAKEOVER'}
 elseif($o.lease_present){$classification='RELEASED_EXPIRED_LEASE'}
 $blockers=@('CURRENT_ORIGINAL_SQL_AND_FENCE_NOT_VERIFIED','CURRENT_ORIGINAL_ROW_LOCK_NOT_ACQUIRED','EXPLICIT_ORIGINAL_DATABASE_START_OR_WRITE_APPROVAL_ABSENT','FRESH_SOURCE_EVIDENCE_REQUIRED_AT_ACTUAL_APPLY','ORIGINAL_WRITER_NOT_IMPLEMENTED_OR_APPROVED','EVENT_OUTBOX_COMPLETION_RECORD_NOT_PREPARED_FOR_REAL_APPLICATION')
 if($classification -ceq 'ACTIVE_LEASE_BLOCKED'){$blockers+='ACTIVE_LEASE_CANNOT_BE_OVERRIDDEN'}
 if($classification -ceq 'OWNER_WITHOUT_LEASE_BLOCKED'){$blockers+='INCONSISTENT_OWNER_LEASE_REQUIRES_REVIEW'}
 if($classification -ceq 'EXPIRED_OWNER_REQUIRES_EXPLICIT_TAKEOVER'){$blockers+='EXPLICIT_BOUND_STALE_OWNER_TAKEOVER_APPROVAL_ABSENT'}
 $script:OwnerProtocolReview=[pscustomobject][ordered]@{policy='UNAPPROVED_OFFLINE_ORIGINAL_RECOVERY_PROTOCOL_V1';context='RESTORED_COPY_SQL_REFERENCE_ONLY';copied_ownership=$o;classification=$classification;candidate=$p;reference_fence=110;proposed_offline_recovery_fence=111;proposed_terminal_owner=$null;proposed_terminal_lease_until=$null;owner_replacement_requires_bound_explicit_approval=$true;expired_lease_alone_authorizes_takeover=$false;ordinary_demo_acquire_is_not_used=$true;required_atomic_records=@('ACCOUNT_JSONB_PATCH','FENCE_CAS','COMPLETION_AUDIT_RECORD','EVENT','OUTBOX');required_original_checks=@('EXACT_STOPPED_WRITER_CONTAINER_IDENTITIES_AND_MOUNTS','EXPLICIT_NETWORKLESS_ORIGINAL_DATABASE_START_SCOPE','IN_TRANSACTION_ADVISORY_LOCK_AND_ACCOUNT_ROW_LOCK','CURRENT_POSTGRES_STATE_TEXT_HASH_AND_OWNERSHIP_ROW_HASH','EXACT_EXPECTED_FENCE_AND_PREVIOUS_OWNER_LEASE','DATABASE_CLOCK_LEASE_EXPIRATION','FRESH_HASH_VERIFIED_CONDITIONAL_SOURCE_BINDING','SAME_PERIOD_AND_REFERENCE_GAP_GUARDS','FULL_RAW_JSONB_PROTECTED_FIELDS_AND_TYPED_MODEL','ATOMIC_EVENT_OUTBOX_AND_UNKNOWN_COMMIT_QUERY_ONLY_RECOVERY','INDEPENDENT_POSTCOMMIT_READ_AND_CLEAN_DATABASE_STOP');source_evidence_context='USER_SCREENSHOT_REPORTED_CONDITIONAL_BINDING_NOT_CURRENT_FRESHNESS_PROOF';blockers=$blockers;current_original_sql_queried=$false;current_original_fence_verified=$false;original_owner_verified=$false;original_row_lock_acquired=$false;owner_lease_takeover_authorized=$false;original_database_start_authorized=$false;original_database_write_authorized=$false;commit_payload_verified=$false;apply_token_created=$false;baseline_adopted=$false;original_apply_ready=$false;request_resolution_authorized=$false;resend_authorized=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
}
