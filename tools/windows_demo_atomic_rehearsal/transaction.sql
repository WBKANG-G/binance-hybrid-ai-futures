-- Synthetic session-local rehearsal only. No persistent relation is writable here.
-- Deliberately NOT an adoption function, source acceptance, or installable migration.
CREATE TEMP TABLE period_accounts (
 account_id text PRIMARY KEY, owner text, fence bigint NOT NULL,
 lease_until timestamptz, state jsonb NOT NULL
);
CREATE TEMP TABLE period_events (
 event_id uuid PRIMARY KEY, account_id text NOT NULL REFERENCES pg_temp.period_accounts,
 fence bigint NOT NULL, kind text NOT NULL, payload jsonb NOT NULL
);
CREATE TEMP TABLE period_outbox (
 event_id uuid PRIMARY KEY REFERENCES pg_temp.period_events, topic text NOT NULL
);

CREATE FUNCTION pg_temp.rehearse_period(
 operation uuid, expected_hash text, expected_fence bigint, fields jsonb,
 evidence_hash text, fault text DEFAULT 'NONE'
) RETURNS text LANGUAGE plpgsql AS $fn$
DECLARE
 row_before pg_temp.period_accounts%ROWTYPE;
 after_state jsonb;
 audit jsonb;
 key text := operation::text;
 intent jsonb;
 recorded jsonb;
 changed integer;
BEGIN
 IF expected_hash !~ '^[0-9a-f]{64}$' OR evidence_hash !~ '^[0-9a-f]{64}$'
    OR expected_hash IS NULL OR evidence_hash IS NULL
    OR expected_fence IS NULL OR expected_fence < 0 OR expected_fence >= 9223372036854775807
    OR operation IS NULL OR fields IS DISTINCT FROM
    '{"daily_key":"2026-11-18","weekly_key":"2026-11-16",
       "daily_base":"4998.74183608","weekly_base":"4998.74183608",
       "reconciled":false,"stream_ready":false}'::jsonb
    OR fault IS NULL OR fault NOT IN ('NONE','AFTER_ACCOUNT','AFTER_EVENT','AFTER_OUTBOX') THEN
   RAISE EXCEPTION 'SIMULATION_INPUT_INVALID';
 END IF;
 SELECT * INTO STRICT row_before FROM pg_temp.period_accounts
 WHERE account_id='DEMO-SYNTHETIC-PERIOD-TRANSACTION' FOR UPDATE;
 intent := jsonb_build_object('policy','SIMULATED_PERIOD_TRANSACTION_V1',
   'simulation_only',true,'applied',false,'source_accepted',false,
   'execution_approved',false,'operation_id',key,'before_state_sha256',expected_hash,
   'expected_fence',expected_fence,'proposed_fence',expected_fence+1,
   'evidence_sha256',evidence_hash,'proposed_fields',fields);

 -- Retry after an unknown outcome must verify the entire committed result.
 IF (row_before.state->'operations') ? key THEN
   recorded := (row_before.state->'operations'->>key)::jsonb;
   IF recorded - 'candidate_financial_state_sha256' IS DISTINCT FROM intent
      OR row_before.fence <> expected_fence+1
      OR row_before.owner IS NOT NULL OR row_before.lease_until IS NOT NULL
      OR NOT (row_before.state @> fields)
      OR row_before.state->'paused' IS DISTINCT FROM 'true'::jsonb
      OR row_before.state->'mode' IS DISTINCT FROM '"DEMO"'::jsonb
      OR encode(sha256(convert_to(jsonb_set(row_before.state,'{operations}',
         (row_before.state->'operations') - key)::text,'UTF8')),'hex')
         IS DISTINCT FROM recorded->>'candidate_financial_state_sha256'
      OR NOT EXISTS (SELECT 1 FROM pg_temp.period_events e
         JOIN pg_temp.period_outbox o USING(event_id)
         WHERE e.event_id=operation AND e.account_id=row_before.account_id
         AND e.fence=row_before.fence AND e.kind='DEMO_PERIOD_SIMULATION'
         AND e.payload=row_before.state AND o.topic='DEMO_AUDIT') THEN
      RAISE EXCEPTION 'SIMULATION_OPERATION_CONFLICT';
   END IF;
   RETURN 'ALREADY_SIMULATED';
 END IF;
 IF row_before.owner IS NOT NULL
    OR (row_before.lease_until IS NOT NULL AND row_before.lease_until > clock_timestamp()) THEN
   RAISE EXCEPTION 'SIMULATION_WRITER_NOT_QUIESCENT';
 END IF;
 IF row_before.fence <> expected_fence
    OR encode(sha256(convert_to(row_before.state::text,'UTF8')),'hex') <> expected_hash THEN
   RAISE EXCEPTION 'SIMULATION_STALE_PREVIEW';
 END IF;
 IF row_before.state->'mode' IS DISTINCT FROM '"DEMO"'::jsonb
    OR row_before.state->'paused' IS DISTINCT FROM 'true'::jsonb
    OR jsonb_typeof(row_before.state->'operations') IS DISTINCT FROM 'object' THEN
   RAISE EXCEPTION 'SIMULATION_PAUSED_DEMO_REQUIRED';
 END IF;
 after_state := row_before.state || fields;
 audit := intent || jsonb_build_object('candidate_financial_state_sha256',
    encode(sha256(convert_to(after_state::text,'UTF8')),'hex'));
 after_state := jsonb_set(after_state,'{operations}',
    (row_before.state->'operations') || jsonb_build_object(key,audit::text));
 IF (after_state - ARRAY['daily_key','weekly_key','daily_base','weekly_base',
     'reconciled','stream_ready','operations']) IS DISTINCT FROM
    (row_before.state - ARRAY['daily_key','weekly_key','daily_base','weekly_base',
     'reconciled','stream_ready','operations'])
    OR ((after_state->'operations') - key) IS DISTINCT FROM row_before.state->'operations' THEN
   RAISE EXCEPTION 'SIMULATION_PROTECTED_STATE_CHANGED';
 END IF;
 UPDATE pg_temp.period_accounts SET state=after_state, fence=expected_fence+1,
    owner=NULL, lease_until=NULL
 WHERE account_id=row_before.account_id AND fence=expected_fence AND state=row_before.state;
 GET DIAGNOSTICS changed = ROW_COUNT;
 IF changed <> 1 THEN RAISE EXCEPTION 'SIMULATION_CAS_FAILED'; END IF;
 IF fault='AFTER_ACCOUNT' THEN RAISE EXCEPTION 'SIMULATION_INJECTED_FAILURE'; END IF;
 INSERT INTO pg_temp.period_events VALUES
    (operation,row_before.account_id,expected_fence+1,'DEMO_PERIOD_SIMULATION',after_state);
 IF fault='AFTER_EVENT' THEN RAISE EXCEPTION 'SIMULATION_INJECTED_FAILURE'; END IF;
 INSERT INTO pg_temp.period_outbox VALUES (operation,'DEMO_AUDIT');
 IF fault='AFTER_OUTBOX' THEN RAISE EXCEPTION 'SIMULATION_INJECTED_FAILURE'; END IF;
 RETURN 'SIMULATED';
END
$fn$;
