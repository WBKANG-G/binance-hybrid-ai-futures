$script:AcceptedSource=$null
function Assert-AcceptedSource([string]$Root,[object]$Source) {
 $receiptPath=Join-Path $Root 'SOURCE_ACCEPTANCE.json';$proposalPath=Join-Path $Root 'SOURCE_PROPOSAL.json'
 if((Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne '__RECEIPT_FILE_SHA__' -or (Get-FileHash -LiteralPath $proposalPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne '__PROPOSAL_FILE_SHA__'){throw 'CURRENT_ACCEPTANCE_HASH_MISMATCH'}
 $r=ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($receiptPath,[Text.Encoding]::UTF8))
 $e=ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($proposalPath,[Text.Encoding]::UTF8))
 if($r.policy -cne 'DEMO_PAUSED_PERIOD_SOURCE_ACCEPTANCE_V1' -or $r.scope -cne 'SOURCE_ACCEPTANCE_ONLY' -or $r.review_id -cne '__REVIEW_ID__' -or $e.review_id -cne $r.review_id -or $r.proposal_sha256 -cne '__PROPOSAL_SHA__' -or $e.proposal_sha256 -cne $r.proposal_sha256 -or $r.source_accepted -isnot [bool] -or -not $r.source_accepted){throw 'CURRENT_ACCEPTANCE_SCOPE_INVALID'}
 foreach($flag in @('original_database_write_authorized','original_database_start_authorized','original_owner_lease_takeover_authorized','existing_guard_bypass_authorized','request_resolution_authorized','resend_authorized','execution_approved','orders_enabled','live_trading_enabled')){if($r.$flag -isnot [bool] -or $r.$flag){throw 'CURRENT_ACCEPTANCE_AUTHORITY_INVALID'}}
 if($r.candidate.day_kst -cne $Source.day_key -or $r.candidate.week_kst -cne $Source.week_key -or $r.candidate.daily_base_usdt -cne $Source.wallet -or $r.candidate.weekly_base_usdt -cne $Source.wallet){throw 'CURRENT_ACCEPTANCE_CANDIDATE_MISMATCH'}
 $script:AcceptedSource=[pscustomobject]@{review_id=$r.review_id;receipt_sha256='__RECEIPT_CANONICAL_SHA__';source_accepted=$true;original_database_write_authorized=$false}
}
function Test-CurrentReferenceProfile([object]$Result) {
 if($Result.current_read -cne 'CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES' -or $null -eq $Result.summary){return $false}
 return ($Result.summary.observations_consistent_with_expected_flat_profile -is [bool] -and $Result.summary.observations_consistent_with_expected_flat_profile -and @($Result.summary.issues).Count -eq 0 -and $Result.saved_source_hashes_verified_before_after -and -not $Result.settings_changed_by_tool -and -not $Result.request_states_changed -and -not $Result.baseline_adopted -and $Result.order_writes -eq 0 -and -not $Result.orders_enabled -and -not $Result.live_trading_enabled)
}

function Test-PreapplyAccount([object]$Result) {
 return ((Test-CurrentReferenceProfile $Result) -and $Result.accepted_period_matches_observed_period -is [bool] -and $Result.accepted_period_matches_observed_period)
}
