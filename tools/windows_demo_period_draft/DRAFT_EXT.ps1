$script:DraftSource=$null
$script:DraftEvidence=$null
function Initialize-PeriodDraftEvidence([object]$Plan) {
 $base=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
 $script:DraftSource=Get-SavedIncome $Plan (Join-Path $base 'WBKANG-DEMO-IncomeReads')
 Add-SavedTrade $script:DraftSource $Plan (Join-Path $base 'WBKANG-DEMO-TradeReads')
 $current=Get-SavedCurrent $script:DraftSource $Plan (Join-Path $base 'WBKANG-DEMO-CurrentReads')
 $digest=Digest ($Plan.manifest_sha256+'|'+$script:DraftSource.receipt_sha256+'|'+$script:DraftSource.trade_receipt_sha256+'|'+$current.receipt_sha256)
 if($script:DraftSource.day_key -cne '2026-11-18' -or $script:DraftSource.week_key -cne '2026-11-16' -or (Income-Decimal $script:DraftSource.wallet) -le 0){throw 'DRAFT_CANDIDATE_SCOPE_INVALID'}
 $script:DraftEvidence=[pscustomobject]@{day=$script:DraftSource.day_key;week=$script:DraftSource.week_key;amount=$script:DraftSource.wallet;sha256=$digest}
 Trades-VerifySaved $script:DraftSource
}
function Assert-PeriodDraft([object]$Account) {
 Assert-CopyFinancial ($Account|Select-Object -Property * -ExcludeProperty transition_draft)
 $d=$Account.transition_draft;$e=$script:DraftEvidence
 if($d.before_state_sha256 -cne $Account.account_state_sha256 -or $d.candidate_day -cne $e.day -or $d.candidate_week -cne $e.week -or $d.candidate_amount -cne $e.amount -or $d.evidence_sha256 -cne $e.sha256){throw 'DRAFT_EVIDENCE_OR_STATE_MISMATCH'}
 foreach($name in @('before_state_sha256','candidate_financial_state_sha256','candidate_draft_state_sha256')){if($d.$name -cnotmatch '^[0-9a-f]{64}$'){throw 'DRAFT_HASH_INVALID'}}
 if($d.review_id -cnotmatch '^period-review-draft-[0-9a-f]{64}$'){throw 'DRAFT_REVIEW_ID_INVALID'}
 foreach($name in @('only_allowed_top_level_changes','candidate_fields_match_proposal','prior_operations_preserved','audit_key_was_absent','audit_entry_is_string','audit_entry_matches_draft','orders_preserved','requests_preserved','valuation_preserved','high_water_preserved','paused_preserved','fresh_reconciliation_required','draft_only_not_a_commit_payload')){if($d.$name -isnot [bool] -or -not $d.$name){throw 'DRAFT_PRESERVATION_CHECK_FAILED'}}
 foreach($name in @('database_changed','source_accepted','typed_ledger_model_validated')){if($d.$name -isnot [bool] -or $d.$name){throw 'DRAFT_UNSUPPORTED_CLAIM'}}
 $allowed=@('daily_key','weekly_key','daily_base','weekly_base','reconciled','stream_ready','operations')
 $changes=@($d.changed_top_level_fields)
 if($changes.Count -lt 3 -or @($changes|Select-Object -Unique).Count -ne $changes.Count -or @($changes|Where-Object {$allowed -cnotcontains $_}).Count -ne 0 -or $changes -cnotcontains 'daily_key' -or $changes -cnotcontains 'weekly_key' -or $changes -cnotcontains 'operations'){throw 'DRAFT_CHANGED_FIELD_SCOPE_INVALID'}
 $fence=Income-Integer $d.expected_fence 'DRAFT_FENCE_INVALID';$nextFence=Income-Integer $d.proposed_fence 'DRAFT_FENCE_INVALID'
 if([decimal]$fence -gt ([decimal][long]::MaxValue-1) -or [decimal]$nextFence -ne ([decimal]$fence+1)){throw 'DRAFT_FENCE_INVALID'}
 $a=$d.audit_draft
 if($a.expected_fence -ne $d.expected_fence -or $a.proposed_fence -ne $d.proposed_fence -or $a.policy -cne 'UNAPPROVED_PERIOD_REVIEW_DRAFT_V1' -or $a.review_id -cne $d.review_id -or $a.before_state_sha256 -cne $d.before_state_sha256 -or $a.candidate_financial_state_sha256 -cne $d.candidate_financial_state_sha256 -or $a.evidence_sha256 -cne $e.sha256 -or $a.source_accepted -isnot [bool] -or $a.source_accepted -or $a.applied -isnot [bool] -or $a.applied -or $a.execution_approved -isnot [bool] -or $a.execution_approved){throw 'DRAFT_AUDIT_INVALID'}
 if($a.proposed_fields.daily_key -cne $e.day -or $a.proposed_fields.weekly_key -cne $e.week -or $a.proposed_fields.daily_base -cne $e.amount -or $a.proposed_fields.weekly_base -cne $e.amount -or $a.proposed_fields.reconciled -isnot [bool] -or $a.proposed_fields.reconciled -or $a.proposed_fields.stream_ready -isnot [bool] -or $a.proposed_fields.stream_ready){throw 'DRAFT_AUDIT_INVALID'}
 Trades-VerifySaved $script:DraftSource
}
