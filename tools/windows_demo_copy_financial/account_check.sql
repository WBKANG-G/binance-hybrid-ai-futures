BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='2s';
SET LOCAL search_path=pg_catalog,public;
SELECT json_build_object(
 'paused',state->'paused', 'daily_key',state->>'daily_key', 'weekly_key',state->>'weekly_key',
 'orders',(SELECT count(*) FROM jsonb_object_keys(state->'orders')),
 'unresolved_requests',(SELECT count(*) FROM jsonb_each_text(state->'requests') WHERE value IN ('UNKNOWN','SUBMITTING','CANCEL_REQUESTED','NOT_FOUND_UNRESOLVED')),
 'account_state_sha256',encode(sha256(convert_to(state::text,'UTF8')),'hex'),
 'transaction_read_only',current_setting('transaction_read_only'),
 'financial',json_build_object('daily_base',state->>'daily_base','weekly_base',state->>'weekly_base','high_water',state->>'high_water','available_balance',state->>'available_balance',
 'initial',state->'value'->>'initial','realized',state->'value'->>'realized','unrealized',state->'value'->>'unrealized','fees',state->'value'->>'fees','funding',state->'value'->>'funding','net_deposits',state->'value'->>'net_deposits','value_as_of',state->'value'->>'as_of'),
 'request_states',(SELECT json_agg(json_build_object('request_reference_sha256',encode(sha256(convert_to(key,'UTF8')),'hex'),'request_state',value) ORDER BY key) FROM jsonb_each_text(state->'requests') WHERE value IN ('UNKNOWN','SUBMITTING','CANCEL_REQUESTED','NOT_FOUND_UNRESOLVED')),
 'risk_state',state->>'risk_state','reconciled',state->'reconciled','stream_ready',state->'stream_ready','last_reconciled',state->>'last_reconciled')
 FROM public.demo_accounts WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1';
COMMIT;
