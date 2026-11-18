"""COPY-only real transaction rehearsal; fixed local clone socket, no exchange I/O."""

import hashlib
import json
import re
import sys

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"


class CopyBlocked(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise CopyBlocked(code)


def snapshot(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT state::text,fence,owner,lease_until FROM public.demo_accounts WHERE account_id=%s",
            (ACCOUNT,),
        )
        rows = cur.fetchall()
        require(len(rows) == 1, "MODEL_COPY_ACCOUNT_NOT_UNIQUE")
        cur.execute("SELECT count(*) FROM public.demo_events")
        events = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM public.demo_outbox")
        outbox = cur.fetchone()[0]
    return rows[0], events, outbox


def candidate_text(cur, before_text, fields, receipt):
    # Never write a Python-roundtripped full ledger: PostgreSQL patches original JSONB.
    cur.execute(
        "SELECT jsonb_set(%s::jsonb || %s::jsonb,'{operations}',"
        "(%s::jsonb->'operations') || jsonb_build_object(%s::text,%s::text))::text",
        (
            before_text,
            json.dumps(fields),
            before_text,
            receipt["operation_id"],
            json.dumps(receipt, sort_keys=True),
        ),
    )
    return cur.fetchone()[0]


def apply_copy(conn, original, expected, receipt, fault=None):
    before_text = original[0][0]
    operation = receipt["operation_id"]
    fields = receipt["committed_fields"]
    with conn.transaction():
        with conn.cursor() as cur:
            # Only this transaction on the host-admitted, newly owned clone may write.
            cur.execute("SET TRANSACTION READ WRITE")
            cur.execute("SET LOCAL lock_timeout='2s'")
            cur.execute("SET LOCAL statement_timeout='5s'")
            cur.execute("SET LOCAL search_path=pg_catalog,public")
            cur.execute(
                "SELECT state::text,fence,owner,lease_until,clock_timestamp() "
                "FROM public.demo_accounts WHERE account_id=%s FOR UPDATE",
                (ACCOUNT,),
            )
            rows = cur.fetchall()
            require(len(rows) == 1, "MODEL_COPY_ACCOUNT_NOT_UNIQUE")
            text, fence, owner, until, now = rows[0]
            proposed = candidate_text(cur, before_text, fields, receipt)
            if operation in json.loads(text)["operations"]:
                require(
                    text == proposed
                    and fence == expected["proposed_fence"]
                    and owner is None
                    and until is None,
                    "MODEL_COPY_OPERATION_CONFLICT",
                )
                cur.execute(
                    "SELECT count(*) FROM public.demo_events e JOIN public.demo_outbox o USING(event_id) "
                    "WHERE e.event_id=%s AND e.account_id=%s AND e.fence=%s "
                    "AND e.kind='DEMO_COPY_PERIOD_REHEARSAL' AND e.payload=%s AND o.topic='DEMO_AUDIT'",
                    (operation, ACCOUNT, fence, text),
                )
                require(cur.fetchone()[0] == 1, "MODEL_COPY_OPERATION_CONFLICT")
                return "ALREADY_COMMITTED"
            require(until is None or until <= now, "MODEL_COPY_ACTIVE_LEASE")
            require(
                fence == expected["expected_fence"]
                and text == before_text
                and hashlib.sha256(text.encode()).hexdigest() == expected["before_state_sha256"],
                "MODEL_COPY_STALE_PREVIEW",
            )
            cur.execute(
                "SELECT encode(sha256(convert_to((state || %s::jsonb)::text,'UTF8')),'hex') "
                "FROM public.demo_accounts WHERE account_id=%s",
                (json.dumps(fields), ACCOUNT),
            )
            require(
                cur.fetchone()[0] == expected["candidate_financial_state_sha256"],
                "MODEL_COPY_FINANCIAL_HASH_MISMATCH",
            )
            cur.execute(
                "UPDATE public.demo_accounts SET state=jsonb_set(state || %s::jsonb,'{operations}',"
                "(state->'operations') || jsonb_build_object(%s::text,%s::text)),fence=%s,owner=NULL,lease_until=NULL "
                "WHERE account_id=%s AND fence=%s "
                "AND encode(sha256(convert_to(state::text,'UTF8')),'hex')=%s RETURNING state::text",
                (
                    json.dumps(fields),
                    operation,
                    json.dumps(receipt, sort_keys=True),
                    expected["proposed_fence"],
                    ACCOUNT,
                    expected["expected_fence"],
                    expected["before_state_sha256"],
                ),
            )
            updated = cur.fetchall()
            require(len(updated) == 1 and updated[0][0] == proposed, "MODEL_COPY_CAS_FAILED")
            if fault == "AFTER_ACCOUNT":
                raise CopyBlocked("MODEL_COPY_INJECTED_FAILURE")
            cur.execute(
                "INSERT INTO public.demo_events(event_id,account_id,fence,as_of,kind,payload) "
                "SELECT %s,account_id,fence,clock_timestamp(),'DEMO_COPY_PERIOD_REHEARSAL',state::text "
                "FROM public.demo_accounts WHERE account_id=%s",
                (operation, ACCOUNT),
            )
            if fault == "AFTER_EVENT":
                raise CopyBlocked("MODEL_COPY_INJECTED_FAILURE")
            cur.execute(
                "INSERT INTO public.demo_outbox(event_id,topic) VALUES (%s,'DEMO_AUDIT')",
                (operation,),
            )
            if fault == "AFTER_OUTBOX":
                raise CopyBlocked("MODEL_COPY_INJECTED_FAILURE")
    # A success value is returned only after transaction context COMMIT succeeds.
    return "COMMITTED"


def rehearse(connect, module, expected):
    with connect() as conn:
        original = snapshot(conn)
        before = json.loads(original[0][0])
        require(
            hashlib.sha256(original[0][0].encode()).hexdigest() == expected["before_state_sha256"]
            and original[0][1] == expected["expected_fence"],
            "MODEL_COPY_STALE_PREVIEW",
        )
        _, receipt = module.build_copy_candidate(before, expected)
        for fault in ("AFTER_ACCOUNT", "AFTER_EVENT", "AFTER_OUTBOX"):
            try:
                apply_copy(conn, original, expected, receipt, fault)
            except CopyBlocked as error:
                if str(error) != "MODEL_COPY_INJECTED_FAILURE":
                    raise
            else:
                raise CopyBlocked("MODEL_COPY_FAILURE_NOT_INJECTED")
            require(snapshot(conn) == original, "MODEL_COPY_PARTIAL_WRITE")
        require(
            apply_copy(conn, original, expected, receipt) == "COMMITTED",
            "MODEL_COPY_COMMIT_NOT_OBSERVED",
        )
        require(
            apply_copy(conn, original, expected, receipt) == "ALREADY_COMMITTED",
            "MODEL_COPY_IDEMPOTENCY_FAILED",
        )
    with connect() as conn:
        after, events, outbox = snapshot(conn)
        with conn.cursor() as cur:
            proposed = candidate_text(cur, original[0][0], receipt["committed_fields"], receipt)
        require(
            after[0] == proposed
            and after[1] == expected["proposed_fence"]
            and events == original[1] + 1
            and outbox == original[2] + 1,
            "MODEL_COPY_POSTCOMMIT_MISMATCH",
        )
        module.DemoLedger.model_validate(json.loads(after[0]))
    return dict(
        context="COPY_TRANSACTION_REHEARSAL_ONLY",
        rollback_cases_verified=3,
        copy_commit_verified=True,
        copy_event_outbox_atomicity_verified=True,
        copy_idempotency_verified=True,
        independent_postcommit_read_verified=True,
        protected_raw_json_preserved=True,
        completion_record_context="COPY_REHEARSAL_ONLY",
        real_application_rejected=True,
        before_state_sha256=expected["before_state_sha256"],
        copy_after_state_sha256=hashlib.sha256(after[0].encode()).hexdigest(),
        source_fence=expected["expected_fence"],
        copy_fence=after[1],
        copy_day=receipt["committed_fields"]["daily_key"],
        copy_week=receipt["committed_fields"]["weekly_key"],
        events_added=1,
        outbox_added=1,
        original_source_accepted=False,
        original_database_changed=False,
        original_baseline_adopted=False,
        request_states_changed=False,
        actual_http_attempts=0,
        order_writes=0,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )


def run(config, module_body, sql_body, read_worker):
    stage = "SEALED_SOURCE_CHECK"
    try:
        require(
            hashlib.sha256(read_worker).hexdigest() == config["read_worker_sha256"],
            "MODEL_COPY_READ_WORKER_HASH_MISMATCH",
        )
        namespace = {"__name__": "copy_read_library"}
        exec(compile(read_worker, "<sealed-read-worker>", "exec"), namespace)
        read_config = {k: v for k, v in config.items() if k != "read_worker_sha256"}
        stage = "READ_ONLY_MODEL_PREFLIGHT"
        result, code = namespace["collect"](read_config, module_body, sql_body)
        require(
            code == 0 and result.get("rehearsal_receipt_rejected_for_real_application") is True,
            "MODEL_COPY_PREFLIGHT_BLOCKED",
        )
        import psycopg

        def connect():
            return psycopg.connect(
                "dbname=hybrid user=dev host=/review-socket",
                autocommit=True,
                connect_timeout=3,
                options="-c default_transaction_read_only=on -c statement_timeout=5000 -c timezone=UTC",
            )

        stage = "COPY_ONLY_TRANSACTION_REHEARSAL"
        module = sys.modules["services.execution.demo_period_draft_model_review"]
        return rehearse(connect, module, config["expected"]), 0
    except Exception as error:
        code = "MODEL_COPY_WORKER_BLOCKED"
        if isinstance(error, CopyBlocked) and re.fullmatch(r"MODEL_[A-Z0-9_]{1,100}", str(error)):
            code = str(error)
        return dict(
            context="COPY_TRANSACTION_REHEARSAL_ONLY",
            failure_category=code,
            failure_stage=stage,
            copy_commit_verified=False,
            original_database_changed=False,
            original_baseline_adopted=False,
            orders_enabled=False,
            live_trading_enabled=False,
        ), 2


if __name__ == "__main__":
    value, status = run(
        globals().get("CONFIG"),
        globals().get("MODULE_BODY", b""),
        globals().get("SQL_BODY", b""),
        globals().get("READ_WORKER", b""),
    )
    print(json.dumps(value, sort_keys=True))
    raise SystemExit(status)
