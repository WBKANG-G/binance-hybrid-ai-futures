WITH original AS (
 SELECT state, fence FROM public.demo_accounts WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1'
), target AS (
 SELECT state, fence AS old_fence,
 jsonb_build_object('daily_key', :'draft_day', 'weekly_key', :'draft_week',
 'daily_base', :'draft_amount', 'weekly_base', :'draft_amount',
 'reconciled', false, 'stream_ready', false) AS fields,
 :'draft_evidence'::text AS evidence
 FROM original
), financial_candidate AS (
 SELECT state, fields, evidence, old_fence, state || fields AS proposed,
 encode(sha256(convert_to(state::text,'UTF8')),'hex') AS before_hash
 FROM target
), audit AS (
 SELECT *, encode(sha256(convert_to(proposed::text,'UTF8')),'hex') AS financial_hash,
 'period-review-draft-' || encode(sha256(convert_to(before_hash || old_fence::text || fields::text || evidence,'UTF8')),'hex') AS review_id
 FROM financial_candidate
), audit_record AS (
 SELECT *, jsonb_build_object('policy','UNAPPROVED_PERIOD_REVIEW_DRAFT_V1',
 'review_id',review_id,'expected_fence',old_fence,'proposed_fence',old_fence+1,'before_state_sha256',before_hash,'candidate_financial_state_sha256',financial_hash,
 'evidence_sha256',evidence,'proposed_fields',fields,
 'source_accepted',false,'applied',false,'execution_approved',false) AS record
 FROM audit
), candidate AS (
 SELECT *, jsonb_set(proposed,'{operations}',(state->'operations') || jsonb_build_object(review_id,record::text)) AS after_state
 FROM audit_record
), transition AS (
 SELECT jsonb_build_object(
 'review_id',review_id,'expected_fence',old_fence,'proposed_fence',old_fence+1,'before_state_sha256',before_hash,
 'candidate_financial_state_sha256',financial_hash,
 'candidate_draft_state_sha256',encode(sha256(convert_to(after_state::text,'UTF8')),'hex'),
 'candidate_day',fields->>'daily_key','candidate_week',fields->>'weekly_key',
 'candidate_amount',fields->>'daily_base','evidence_sha256',evidence,
 'changed_top_level_fields',(SELECT jsonb_agg(name ORDER BY name) FROM (
   SELECT name FROM jsonb_object_keys(state || after_state) AS name WHERE state->name IS DISTINCT FROM after_state->name
 ) AS changed),
 'only_allowed_top_level_changes',(state - ARRAY['daily_key','weekly_key','daily_base','weekly_base','reconciled','stream_ready','operations']) = (after_state - ARRAY['daily_key','weekly_key','daily_base','weekly_base','reconciled','stream_ready','operations']),
 'prior_operations_preserved', ((after_state->'operations') - review_id) = state->'operations',
 'audit_key_was_absent', NOT ((state->'operations') ? review_id),
 'audit_entry_is_string',jsonb_typeof(after_state->'operations'->review_id)='string',
 'audit_entry_matches_draft',(after_state->'operations'->>review_id)::jsonb=record,
 'orders_preserved',state->'orders'=after_state->'orders',
 'requests_preserved',state->'requests'=after_state->'requests',
 'valuation_preserved',state->'value'=after_state->'value',
 'high_water_preserved',state->'high_water'=after_state->'high_water',
 'paused_preserved',state->'paused'=after_state->'paused' AND after_state->'paused'='true'::jsonb,
 'fresh_reconciliation_required',after_state->'reconciled'='false'::jsonb AND after_state->'stream_ready'='false'::jsonb,
 'audit_draft',record,'draft_only_not_a_commit_payload',true,
 'database_changed',false,'source_accepted',false,'typed_ledger_model_validated',false
 ) AS report FROM candidate
)
