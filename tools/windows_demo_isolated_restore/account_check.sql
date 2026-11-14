BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='2s';
SET LOCAL search_path=pg_catalog,public;
SELECT json_build_object(
 'paused',state->'paused',
 'daily_key',state->>'daily_key',
 'weekly_key',state->>'weekly_key',
 'orders',(SELECT count(*) FROM jsonb_object_keys(state->'orders')),
 'unresolved_requests',(SELECT count(*) FROM jsonb_each_text(state->'requests') WHERE value IN ('UNKNOWN','SUBMITTING','CANCEL_REQUESTED','NOT_FOUND_UNRESOLVED')),
 'account_state_sha256',encode(sha256(convert_to(state::text,'UTF8')),'hex'),
 'transaction_read_only',current_setting('transaction_read_only'))
 FROM public.demo_accounts WHERE account_id='DEMO-WINDOWS-ISOLATED-BTC-V1';
COMMIT;
