"""Bind durable local quotas to original fenced effects. No activation CLI.

Reservations cover the whole flow until the original full flat proof. Six slots
are charged at closure, including unused slots, so delayed sends/triggers are not
expired based on the earlier intent timestamp. External account usage is unknown.
"""

import asyncio
import hashlib
import json
import threading
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from pydantic import SecretStr

from integrations.binance_usdm.demo_tp_window_budget import WindowTPBudget
from integrations.binance_usdm.rest import RESTResponse
from packages.domain.contracts import Signal
from packages.persistence.demo import DemoStore
from packages.persistence.demo_order_quota import (
    DEFAULT_LOCAL_POLICY,
    FLOW_SLOTS,
    DurableOrderQuota,
    LocalQuotaPolicy,
    LocalQuotaStopped,
    canonical,
    quota_require,
)
from services.execution.demo_models import DemoLedger
from services.execution.demo_order_admission import published_order_windows
from services.execution.demo_tp_admission_candidate import run_admission_candidate
from services.execution.demo_tp_budget_integration import BudgetedTPTriggerCoordinator
from services.execution.demo_tp_uncertain_cancel_candidate import UncertainCancellationClient
from services.execution.models import Lease, PaperOrder
from services.risk.paper import GateSnapshot

STORAGE_KEY = "MANAGED_QUOTA:STORAGE_V1"
RESERVE_PREFIX = "MANAGED_QUOTA:RESERVE:"
SEAL_PREFIX = "MANAGED_QUOTA:SEAL:"
EFFECTS = {
    "ENTRY": "DEMO_ENTRY_EFFECT",
    "SL": "DEMO_SL_EFFECT",
    "TP": "DEMO_TP_EFFECT",
    "EXIT": "DEMO_REDUCE_ONLY_EFFECT",
}
T = TypeVar("T")


def roles_for(account: str, flow: UUID) -> dict[str, str]:
    tail = hashlib.sha256((account + str(flow)).encode()).hexdigest()[:30]
    return {"ENTRY": "DE-" + tail, "SL": "DS-" + tail, "TP": "DT-" + tail, "EXIT": "DX-" + tail}


def record_ref(record: dict[str, Any]) -> dict[str, Any]:
    ref = {
        "event_id": record["id"],
        "event_hash": record["hash"],
        "seq": record["seq"],
        "journal": record["data"]["journal"],
        "policy": record["data"]["policy"],
        "flow": record["data"]["flow"],
    }
    if record["kind"] == "RESERVE":
        ref.update({"roles": record["data"]["roles"], "slots": FLOW_SLOTS})
    return ref


def ledger_hash(ledger: DemoLedger) -> str:
    return hashlib.sha256(ledger.model_dump_json().encode()).hexdigest()


@dataclass(frozen=True)
class ManagedEffect:
    bridge: "ManagedQuotaBridge"
    lease: Lease
    kind: str
    ledger: DemoLedger


CURRENT_EFFECT: ContextVar[ManagedEffect | None] = ContextVar("managed_quota_effect", default=None)


class ManagedQuotaBridge:
    def __init__(self, store: DemoStore, lease: Lease, journal: DurableOrderQuota):
        self.store, self.lease, self.journal = store, lease, journal
        self.degraded = False
        self.authorization_lock = threading.RLock()
        self.exit_fallbacks = 0
        self.attempted_ids: set[str] = set()
        self.current_flow: UUID | None = None

        def start(ledger: DemoLedger) -> None:
            self.validate_bindings(ledger)
            for f in journal.flow_records().values():
                for role in f["claims"]:
                    self.attempted_ids.add(f["reserve"]["data"]["roles"][role])
            journal.start_session(lease.owner, lease.fence)

        store.change(lease, "DEMO_MANAGED_QUOTA_SESSION", start)

    @staticmethod
    def bootstrap(
        store: DemoStore,
        lease: Lease,
        directory: Path,
        policy: LocalQuotaPolicy = DEFAULT_LOCAL_POLICY,
        **kwargs: Any,
    ) -> DurableOrderQuota:
        created: DurableOrderQuota | None = None

        def bind(ledger: DemoLedger) -> None:
            nonlocal created
            quota_require(STORAGE_KEY not in ledger.operations, "QUOTA_STORAGE_ALREADY_BOUND")
            quota_require(
                ledger.paused
                and ledger.risk_state.value == "NORMAL"
                and not any(ledger.positions.values())
                and all(
                    not o.remaining and not o.open_quantity and o.position_state.value == "CLOSED"
                    for o in ledger.orders.values()
                ),
                "QUOTA_BOOTSTRAP_REQUIRES_PAUSED_FLAT",
            )
            identity = uuid4()
            created = DurableOrderQuota.create(
                directory, lease.account_id, identity, policy, **kwargs
            )
            ledger.operations[STORAGE_KEY] = canonical(
                {
                    "journal": str(identity),
                    "policy": policy.digest,
                    "scope": "MANAGED_WRITERS_ONLY",
                    "external_usage": "UNKNOWN",
                }
            )

        try:
            store.change(lease, "DEMO_MANAGED_QUOTA_STORAGE_BOUND", bind)
            assert created is not None
            return created
        except Exception:
            if created is not None:
                created.close()
            raise

    def validate_bindings(self, ledger: DemoLedger, *, pending: UUID | None = None) -> None:
        expected = canonical(
            {
                "journal": str(self.journal.journal_id),
                "policy": self.journal.policy.digest,
                "scope": "MANAGED_WRITERS_ONLY",
                "external_usage": "UNKNOWN",
            }
        )
        quota_require(
            ledger.operations.get(STORAGE_KEY) == expected, "QUOTA_STORAGE_BINDING_MISMATCH"
        )
        flows = self.journal.flow_records()
        pg_flows = {
            k[len(RESERVE_PREFIX) :] for k in ledger.operations if k.startswith(RESERVE_PREFIX)
        }
        quota_require(
            pg_flows == set(flows) - ({str(pending)} if pending is not None else set()),
            "QUOTA_PG_JOURNAL_FLOW_MISMATCH",
        )
        for flow, state in flows.items():
            if pending is not None and flow == str(pending):
                continue
            quota_require(
                ledger.operations.get(RESERVE_PREFIX + flow)
                == canonical(record_ref(state["reserve"])),
                "QUOTA_PG_JOURNAL_FLOW_MISMATCH",
            )
            seal = state["seal"]
            quota_require(
                ledger.operations.get(SEAL_PREFIX + flow)
                == (canonical(record_ref(seal)) if seal else None),
                "QUOTA_PG_JOURNAL_SEAL_MISMATCH",
            )
        quota_require(
            all(
                k[len(SEAL_PREFIX) :] in pg_flows
                for k in ledger.operations
                if k.startswith(SEAL_PREFIX)
            ),
            "QUOTA_PG_JOURNAL_SEAL_MISMATCH",
        )

    def reserve(self, flow: UUID) -> None:
        quota_require(not self.degraded, "QUOTA_BRIDGE_DEGRADED")
        self.validate_bindings(self.store.read(self.lease.account_id))
        record = self.journal.reserve(flow, roles_for(self.lease.account_id, flow))

        def bind(ledger: DemoLedger) -> None:
            self.validate_bindings(ledger, pending=flow)
            quota_require(
                str(flow) not in ledger.orders
                and RESERVE_PREFIX + str(flow) not in ledger.operations,
                "QUOTA_FLOW_ALREADY_EXISTS",
            )
            ledger.operations[RESERVE_PREFIX + str(flow)] = canonical(record_ref(record))

        self.store.change(self.lease, "DEMO_MANAGED_QUOTA_FLOW_RESERVED", bind)
        self.current_flow = flow

    def authorize(self, method: str, path: str, params: dict[str, list[str]]) -> None:
        if method != "POST" or path not in {"/fapi/v1/order", "/fapi/v1/algoOrder"}:
            return
        context = CURRENT_EFFECT.get()
        quota_require(
            context is not None and context.bridge is self and context.lease == self.lease,
            "QUOTA_FENCED_EFFECT_REQUIRED",
        )
        assert context is not None
        ledger = context.ledger
        if path == "/fapi/v1/order":
            role = "EXIT" if params.get("reduceOnly") == ["true"] else "ENTRY"
            identifier = params.get("newClientOrderId", [""])[0]
        else:
            role = (
                "SL"
                if params.get("type") == ["STOP_MARKET"]
                else "TP"
                if params.get("type") == ["TAKE_PROFIT_MARKET"]
                else "INVALID"
            )
            identifier = params.get("clientAlgoId", [""])[0]
        quota_require(
            role in EFFECTS and context.kind == EFFECTS[role], "QUOTA_EFFECT_ROLE_MISMATCH"
        )
        matches = [
            (key, order)
            for key, order in ledger.orders.items()
            if roles_for(self.lease.account_id, order.signal.signal_id).get(role) == identifier
        ]
        quota_require(len(matches) == 1, "QUOTA_ORDER_OWNERSHIP_INVALID")
        flow_key, order = matches[0]
        flow = UUID(flow_key)
        quota_require(
            ledger.requests.get(identifier) == "SUBMITTING", "QUOTA_COMMITTED_INTENT_REQUIRED"
        )
        quota_require(
            RESERVE_PREFIX + flow_key in ledger.operations
            and SEAL_PREFIX + flow_key not in ledger.operations,
            "QUOTA_COMMITTED_FLOW_REQUIRED",
        )
        reserve = json.loads(ledger.operations[RESERVE_PREFIX + flow_key])
        storage = json.loads(ledger.operations.get(STORAGE_KEY, "{}"))
        quota_require(
            reserve.get("journal") == storage.get("journal") == str(self.journal.journal_id)
            and reserve.get("policy") == self.journal.policy.digest
            and reserve.get("flow") == flow_key
            and reserve.get("slots") == FLOW_SLOTS
            and reserve.get("roles") == roles_for(self.lease.account_id, flow),
            "QUOTA_STORAGE_BINDING_MISMATCH",
        )
        quota_require(identifier not in self.attempted_ids, "QUOTA_PROCESS_ATTEMPT_DUPLICATE")
        if role == "EXIT":
            quota_require(
                order.exit_id == identifier
                and order.exit_state == "SUBMITTING"
                and order.open_quantity > 0,
                "QUOTA_EXIT_INTENT_REQUIRED",
            )
        if role == "SL":
            quota_require(order.algo_id == identifier, "QUOTA_PROTECTION_INTENT_REQUIRED")
        if role == "TP":
            quota_require(order.tp_algo_id == identifier, "QUOTA_PROTECTION_INTENT_REQUIRED")
        quota_require(not self.degraded or role == "EXIT", "QUOTA_BRIDGE_DEGRADED")
        try:
            self.journal.claim(flow, role, identifier)
        except Exception as error:
            self.degraded = True
            nonrecoverable = isinstance(error, LocalQuotaStopped) and str(error) in {
                "QUOTA_EVENT_ALREADY_CONSUMED",
                "QUOTA_PROCESS_OWNERSHIP_INVALID",
                "QUOTA_FLOW_NOT_ACTIVE",
                "QUOTA_ROLE_SCOPE_INVALID",
                "QUOTA_RECOVERY_EXIT_ONLY",
            }
            # No count credit is created: the precommitted PostgreSQL flow still
            # reserves all six slots. Only the original one-shot owned exit can
            # use this fallback; an inherited/lost process lock cannot use it.
            quota_require(
                not nonrecoverable and role == "EXIT" and self.journal.owns_process_lock(),
                "QUOTA_JOURNAL_UNAVAILABLE",
            )
            self.exit_fallbacks += 1
            ledger.operations["MANAGED_QUOTA:EXIT_FALLBACK:" + flow_key] = canonical(
                {
                    "cause": "JOURNAL_UNAVAILABLE",
                    "identifier": identifier,
                    "reserved_slots": FLOW_SLOTS,
                }
            )
        self.attempted_ids.add(identifier)

    def seal_verified(self, ledger_before: DemoLedger) -> None:
        flow = self.current_flow
        quota_require(flow is not None, "QUOTA_FLOW_NOT_ACTIVE")
        assert flow is not None
        expected = ledger_hash(ledger_before)

        def seal(ledger: DemoLedger) -> None:
            quota_require(
                ledger_hash(ledger) == expected
                and BudgetedTPTriggerCoordinator._locally_flat(ledger),
                "QUOTA_FULL_PROOF_CHANGED",
            )
            self.validate_bindings(ledger)
            key = SEAL_PREFIX + str(flow)
            if key in ledger.operations:
                return  # Same full proof may repeat; never move the seal clock.
            record = self.journal.seal(flow)
            ledger.operations[key] = canonical(record_ref(record))

        self.store.change(self.lease, "DEMO_MANAGED_QUOTA_FULL_PROOF_SEALED", seal)

    async def wait_ready(self) -> None:
        while True:
            v = self.journal.view()
            quota_require(v["clock_trusted"], "QUOTA_CLOCK_UNTRUSTED")
            if v["local_window_ready"]:
                return
            await asyncio.to_thread(self.store.renew, self.lease)
            await asyncio.sleep(
                min(1.0, max(0.001, v["cold_start_hold_seconds"] - v["hold_elapsed_seconds"]))
            )

    def diagnostics(self) -> dict[str, Any]:
        try:
            report = self.journal.view()
        except Exception:
            report = {
                "scope": "MANAGED_WRITERS_WITH_SHARED_JOURNAL_ONLY",
                "journal_readable": False,
                "external_account_usage": "UNKNOWN",
                "execution_approved": False,
            }
        report.update(
            {
                "bridge_degraded": self.degraded,
                "exit_fallbacks": self.exit_fallbacks,
                "attempts_authorized_in_process": len(self.attempted_ids),
                "storage_is_independent_of_PG_effect_transaction": True,
            }
        )
        return report


class ManagedQuotaStore(DemoStore):
    def __init__(self, base: DemoStore, bridge: ManagedQuotaBridge):
        super().__init__(base.engine, clock=base.now)
        self.bridge = bridge

    def change(self, lease: Lease, kind: str, apply: Callable[[DemoLedger], T]) -> T:
        def scoped(ledger: DemoLedger) -> T:
            token = CURRENT_EFFECT.set(ManagedEffect(self.bridge, lease, kind, ledger))
            try:
                return apply(ledger)
            finally:
                CURRENT_EFFECT.reset(token)

        return super().change(lease, kind, scoped)


class ManagedOrderWindowBudget(WindowTPBudget):
    def __init__(self, *args: Any, bridge: ManagedQuotaBridge, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.bridge = bridge

    def configure(self, info: Any) -> None:
        super().configure(info)
        limits = published_order_windows(info)
        quota_require(
            limits[("SECOND", 10)] >= self.bridge.journal.policy.slots_10s
            and limits[("MINUTE", 1)] >= self.bridge.journal.policy.slots_60s,
            "QUOTA_PUBLISHED_LIMIT_TOO_LOW",
        )

    def __call__(
        self, method: str, path: str, query: SecretStr, key: SecretStr | None
    ) -> RESTResponse:
        if method != "POST" or path not in {"/fapi/v1/order", "/fapi/v1/algoOrder"}:
            return super().__call__(method, path, query, key)
        # Journal authorization commits before entering the original weight gate.
        # A later weight/local rejection remains conservatively charged; no
        # fabricated response or transport-failure conversion is introduced.
        with self.bridge.authorization_lock:
            try:
                self.bridge.authorize(
                    method, path, parse_qs(query.get_secret_value(), keep_blank_values=True)
                )
            except Exception:
                with self.lock:
                    self._mark("MANAGED_ORDER_QUOTA_STOPPED")
                    self.denied += 1
                raise LocalQuotaStopped("Managed quota denied before transport") from None
            return super().__call__(method, path, query, key)


class ManagedQuotaTPTriggerCoordinator(BudgetedTPTriggerCoordinator):
    def __init__(
        self,
        store: DemoStore,
        lease: Lease,
        client: UncertainCancellationClient,
        budget: ManagedOrderWindowBudget,
        **kwargs: Any,
    ):
        self.quota_bridge = budget.bridge
        quota_require(
            self.quota_bridge.lease == lease and self.quota_bridge.store is store,
            "QUOTA_CONTEXT_MISMATCH",
        )
        super().__init__(
            ManagedQuotaStore(store, self.quota_bridge), lease, client, budget, **kwargs
        )
        self.quota_seal_failed = False

    def reserve(
        self,
        signal: Signal,
        gate: GateSnapshot,
        *,
        quantity_cap: Decimal | None = None,
        entry_style: str = "OPPOSITE_QUOTE",
    ) -> PaperOrder:
        self.quota_bridge.reserve(signal.signal_id)
        return super().reserve(signal, gate, quantity_cap=quantity_cap, entry_style=entry_style)

    def _candidate_confirmation_attempt(self) -> str:
        result = super()._candidate_confirmation_attempt()
        if result == "VERIFIED_FLAT" and self.quota_bridge.current_flow is not None:
            try:
                self.quota_bridge.seal_verified(self.store.read(self.lease.account_id))
            except Exception:
                self.quota_bridge.degraded = True
                self.quota_seal_failed = True
                self.session_failed = True  # Physical flat proof does not approve more entries.
        return result


async def run_managed_quota_candidate(
    coordinator: ManagedQuotaTPTriggerCoordinator, operation: UUID
) -> dict[str, Any]:
    await coordinator.quota_bridge.wait_ready()
    result = await run_admission_candidate(coordinator, operation)
    result["managed_order_quota"] = coordinator.quota_bridge.diagnostics()
    return result
