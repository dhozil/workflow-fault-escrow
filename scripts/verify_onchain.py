"""Deterministic on-chain verification for WorkflowFaultEscrow (studionet).

Covers funding invariants, worker/client authorization, artifact binding and reuse,
accept/failure lifecycle guards, and the time-based fallback settlement. The
consensus attribution path is excluded here (it needs full validator agreement);
see the repository's full-consensus transactions.

Usage (from this container folder):

  python scripts/verify_workflow_fault_escrow_onchain.py \
      --contract workflow-fault-escrow/contracts/workflow_fault_escrow.py
  python scripts/verify_workflow_fault_escrow_onchain.py --address 0x...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import genlayer_py as g  # noqa: E402
from genlayer_py import accounts  # noqa: E402
from genlayer_py.types.transactions import TransactionStatus  # noqa: E402

NETWORK = "studionet"
STAMP = int(time.time())
WF_ID = f"wfe-{STAMP}"
LATE_WF = f"{WF_ID}-late"
AMOUNTS = [600, 400]
TOTAL = sum(AMOUNTS)
SPECS = [
    "Stage 1 delivers a JSON schema with keys service and port.",
    "Stage 2 delivers a migration file that references stage 1 keys.",
]
ARTIFACT_A_URL = "https://raw.githubusercontent.com/genlayerlabs/genvm/main/LICENSE"
REPORT_URL = "https://raw.githubusercontent.com/genlayerlabs/genvm/main/README.md"
ARTIFACT_A_SHA = hashlib.sha256(b"stage-one-artifact").hexdigest()
ARTIFACT_B_SHA = hashlib.sha256(b"stage-two-artifact").hexdigest()
REPORT_SHA = hashlib.sha256(b"failure-report").hexdigest()


def _load_private_key() -> str:
    key = os.environ.get("STUDIONET_PRIVATE_KEY", "").strip()
    if key:
        return key
    path = ROOT / ".wallets" / f"{NETWORK}.json"
    if not path.exists():
        raise SystemExit(
            f"No key found. Set STUDIONET_PRIVATE_KEY or create {path} with a "
            '"private_key" field.'
        )
    return json.loads(path.read_text(encoding="utf-8"))["private_key"]


class Harness:
    def __init__(self) -> None:
        self.client_acct = accounts.create_account(_load_private_key())
        self.worker_a = accounts.create_account()
        self.worker_b = accounts.create_account()
        self.stranger = accounts.create_account()
        self.client = g.create_client(
            chain=getattr(g, NETWORK), account=self.client_acct
        )
        self._schema: dict[str, dict] = {}
        self.addr = ""
        self.passed = 0
        self.failed = 0

    def note(self, label: str, ok: bool, extra: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {label}{(': ' + extra) if extra else ''}", flush=True)
        if ok:
            self.passed += 1
        else:
            self.failed += 1

    def _order(self, fn: str) -> list[str]:
        if self.addr not in self._schema:
            schema = self.client.get_contract_schema(self.addr)
            self._schema[self.addr] = schema.get("methods", {})
        params = self._schema[self.addr].get(fn, {}).get("params", [])
        return [p[0] for p in params]

    def _args(self, fn: str, kw: dict) -> list:
        return [kw.get(name) for name in self._order(fn)]

    def _wait(self, tx: str) -> dict:
        return self.client.wait_for_transaction_receipt(
            tx, status=TransactionStatus.ACCEPTED, interval=2000, retries=90
        )

    def _submit(self, fn: str, account, value: int, kw: dict) -> str:
        last_exc = None
        for attempt in range(6):
            try:
                return self.client.write_contract(
                    address=self.addr,
                    function_name=fn,
                    account=account,
                    value=value,
                    leader_only=True,
                    args=self._args(fn, kw),
                )
            except Exception as exc:  # transient studio 502/504 / rate limits
                last_exc = exc
                time.sleep(4 * (attempt + 1))
        raise last_exc

    def write(self, fn: str, *, account=None, value=0, **kw) -> dict:
        tx = self._submit(fn, account or self.client_acct, value, kw)
        return self._wait(tx)

    def fire(self, fn: str, *, account=None, value=0, **kw) -> str:
        return self._submit(fn, account or self.client_acct, value, kw)

    def read(self, fn: str, **kw):
        last_exc = None
        for attempt in range(5):
            try:
                return self.client.read_contract(
                    address=self.addr, function_name=fn, args=self._args(fn, kw)
                )
            except Exception as exc:  # transient studio 502/504 or rate limits
                last_exc = exc
                time.sleep(3 * (attempt + 1))
        raise last_exc

    def _snapshot(self, view: str, view_kw: dict) -> str:
        try:
            return json.dumps(self.read(view, **view_kw), default=str, sort_keys=True)
        except Exception as exc:
            return f"ERROR:{exc}"

    def guard(self, label: str, view: str, view_kw: dict, fn: str, kw: dict,
              *, account=None, value=0) -> None:
        before = self._snapshot(view, view_kw)
        self.fire(fn, account=account, value=value, **kw)
        after = self._snapshot(view, view_kw)
        self.note(f"guard: {label}", before == after)

    def create_kwargs(self, workflow_id: str, window: int, failure: int, grace: int) -> dict:
        return {
            "workflow_id": workflow_id,
            "workers_json": json.dumps([self.worker_a.address, self.worker_b.address]),
            "amounts_json": json.dumps(AMOUNTS),
            "stage_specs_json": json.dumps(SPECS),
            "submit_window_seconds": window,
            "failure_window_seconds": failure,
            "settlement_grace_seconds": grace,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="contracts/workflow_fault_escrow.py")
    parser.add_argument("--address", default="")
    args = parser.parse_args(argv)

    h = Harness()
    if args.address:
        h.addr = args.address.strip()
    else:
        code = (ROOT / args.contract).read_text(encoding="utf-8")
        tx = h.client.deploy_contract(
            code=code, account=h.client_acct, leader_only=True
        )
        receipt = h._wait(tx)
        h.addr = receipt.get("to_address") or receipt.get("recipient")
        if not h.addr:
            raise SystemExit(f"deploy failed: {receipt}")
    print(f"Contract: {h.addr}\nWorkflow: {WF_ID}\n", flush=True)

    nil = "0x0000000000000000000000000000000000000000"

    # A) funding invariant (workflow does not exist yet)
    h.guard(
        "funding below stage total",
        "get_workflow",
        {"workflow_id": WF_ID},
        "create_workflow",
        h.create_kwargs(WF_ID, 3600, 600, 600),
        value=TOTAL - 1,
    )

    h.write(
        "create_workflow",
        value=TOTAL,
        **h.create_kwargs(WF_ID, 3600, 600, 600),
    )
    wf = h.read("get_workflow", workflow_id=WF_ID)
    h.note(
        "create_workflow funded escrow",
        wf.get("status") == "open" and int(wf.get("total_amount", 0)) == TOTAL,
        f"status={wf.get('status')} total={wf.get('total_amount')}",
    )
    stages = h.read("get_workflow_stages", workflow_id=WF_ID).get("stages", [])
    h.note(
        "stages stored with workers and tranches",
        [int(s.get("amount", 0)) for s in stages] == AMOUNTS
        and [s.get("worker", "").lower() for s in stages]
        == [h.worker_a.address.lower(), h.worker_b.address.lower()],
    )

    # B) creation guards
    h.guard(
        "zero-address worker",
        "get_workflow",
        {"workflow_id": f"{WF_ID}-zero"},
        "create_workflow",
        {
            "workflow_id": f"{WF_ID}-zero",
            "workers_json": json.dumps([nil, h.worker_b.address]),
            "amounts_json": json.dumps(AMOUNTS),
            "stage_specs_json": json.dumps(SPECS),
            "submit_window_seconds": 3600,
            "failure_window_seconds": 600,
            "settlement_grace_seconds": 600,
        },
        value=TOTAL,
    )
    h.guard(
        "duplicate worker addresses",
        "get_workflow",
        {"workflow_id": f"{WF_ID}-dup"},
        "create_workflow",
        {
            "workflow_id": f"{WF_ID}-dup",
            "workers_json": json.dumps([h.worker_a.address, h.worker_a.address]),
            "amounts_json": json.dumps(AMOUNTS),
            "stage_specs_json": json.dumps(SPECS),
            "submit_window_seconds": 3600,
            "failure_window_seconds": 600,
            "settlement_grace_seconds": 600,
        },
        value=TOTAL,
    )
    h.guard(
        "client listed as a worker",
        "get_workflow",
        {"workflow_id": f"{WF_ID}-self"},
        "create_workflow",
        {
            "workflow_id": f"{WF_ID}-self",
            "workers_json": json.dumps([h.client_acct.address, h.worker_b.address]),
            "amounts_json": json.dumps(AMOUNTS),
            "stage_specs_json": json.dumps(SPECS),
            "submit_window_seconds": 3600,
            "failure_window_seconds": 600,
            "settlement_grace_seconds": 600,
        },
        value=TOTAL,
    )
    h.guard(
        "duplicate workflow id",
        "get_workflow",
        {"workflow_id": WF_ID},
        "create_workflow",
        h.create_kwargs(WF_ID, 3600, 600, 600),
        value=TOTAL,
    )

    # C) worker authorization and artifact binding
    h.guard(
        "stranger submits a stage",
        "get_stage",
        {"workflow_id": WF_ID, "index": 0},
        "submit_stage",
        {
            "workflow_id": WF_ID,
            "index": 0,
            "artifact_url": ARTIFACT_A_URL,
            "artifact_sha256": ARTIFACT_A_SHA,
        },
        account=h.stranger,
    )
    h.guard(
        "other stage worker submits this stage",
        "get_stage",
        {"workflow_id": WF_ID, "index": 0},
        "submit_stage",
        {
            "workflow_id": WF_ID,
            "index": 0,
            "artifact_url": ARTIFACT_A_URL,
            "artifact_sha256": ARTIFACT_A_SHA,
        },
        account=h.worker_b,
    )

    h.write(
        "submit_stage",
        account=h.worker_a,
        workflow_id=WF_ID,
        index=0,
        artifact_url=ARTIFACT_A_URL,
        artifact_sha256=ARTIFACT_A_SHA,
    )
    stage = h.read("get_stage", workflow_id=WF_ID, index=0)
    expected = hashlib.sha256(
        f"{WF_ID}|0|{ARTIFACT_A_URL}|{ARTIFACT_A_SHA}".encode()
    ).hexdigest()
    h.note(
        "worker submits hash-bound artifact",
        stage.get("status") == "submitted" and stage.get("artifact_binding") == expected,
        f"binding={stage.get('artifact_binding')}",
    )

    h.guard(
        "double artifact submission",
        "get_stage",
        {"workflow_id": WF_ID, "index": 0},
        "submit_stage",
        {
            "workflow_id": WF_ID,
            "index": 0,
            "artifact_url": ARTIFACT_A_URL,
            "artifact_sha256": ARTIFACT_A_SHA,
        },
        account=h.worker_a,
    )
    h.guard(
        "artifact reused for another stage",
        "get_stage",
        {"workflow_id": WF_ID, "index": 1},
        "submit_stage",
        {
            "workflow_id": WF_ID,
            "index": 1,
            "artifact_url": ARTIFACT_A_URL,
            "artifact_sha256": ARTIFACT_A_SHA,
        },
        account=h.worker_b,
    )
    h.guard(
        "private/local artifact host",
        "get_stage",
        {"workflow_id": WF_ID, "index": 1},
        "submit_stage",
        {
            "workflow_id": WF_ID,
            "index": 1,
            "artifact_url": "http://127.0.0.1/artifact.json",
            "artifact_sha256": ARTIFACT_B_SHA,
        },
        account=h.worker_b,
    )

    # D) client decisions
    h.guard(
        "stranger accepts a stage",
        "get_stage",
        {"workflow_id": WF_ID, "index": 0},
        "accept_stage",
        {"workflow_id": WF_ID, "index": 0},
        account=h.stranger,
    )
    h.write("accept_stage", workflow_id=WF_ID, index=0)
    accepted = h.read("get_stage", workflow_id=WF_ID, index=0)
    wf = h.read("get_workflow", workflow_id=WF_ID)
    h.note(
        "client accepts a stage",
        accepted.get("status") == "accepted"
        and int(wf.get("released_amount", 0)) == AMOUNTS[0],
        f"released={wf.get('released_amount')}",
    )
    h.guard(
        "double accept",
        "get_stage",
        {"workflow_id": WF_ID, "index": 0},
        "accept_stage",
        {"workflow_id": WF_ID, "index": 0},
    )
    h.guard(
        "failure claim against a pending stage",
        "get_workflow",
        {"workflow_id": WF_ID},
        "file_failure",
        {
            "workflow_id": WF_ID,
            "failing_index": 1,
            "report_url": REPORT_URL,
            "report_sha256": REPORT_SHA,
        },
    )

    h.write(
        "submit_stage",
        account=h.worker_b,
        workflow_id=WF_ID,
        index=1,
        artifact_url=ARTIFACT_A_URL,
        artifact_sha256=ARTIFACT_B_SHA,
    )
    h.guard(
        "stranger files a failure",
        "get_workflow",
        {"workflow_id": WF_ID},
        "file_failure",
        {
            "workflow_id": WF_ID,
            "failing_index": 1,
            "report_url": REPORT_URL,
            "report_sha256": REPORT_SHA,
        },
        account=h.stranger,
    )
    h.write(
        "file_failure",
        workflow_id=WF_ID,
        failing_index=1,
        report_url=REPORT_URL,
        report_sha256=REPORT_SHA,
    )
    wf = h.read("get_workflow", workflow_id=WF_ID)
    expected_failure = hashlib.sha256(
        f"{WF_ID}|failure|1|{REPORT_URL}|{REPORT_SHA}".encode()
    ).hexdigest()
    h.note(
        "client files hash-bound failure claim",
        wf.get("status") == "claimed"
        and int(wf.get("failing_index", -2)) == 1
        and wf.get("failure_binding") == expected_failure,
        f"status={wf.get('status')} failing={wf.get('failing_index')}",
    )
    h.guard(
        "double failure claim",
        "get_workflow",
        {"workflow_id": WF_ID},
        "file_failure",
        {
            "workflow_id": WF_ID,
            "failing_index": 0,
            "report_url": REPORT_URL,
            "report_sha256": REPORT_SHA,
        },
    )
    h.guard(
        "settle while the claim is still resolvable",
        "get_workflow",
        {"workflow_id": WF_ID},
        "settle_workflow",
        {"workflow_id": WF_ID},
        account=h.stranger,
    )

    # E) time-based fallback settlement (no claim): pays submitted, refunds pending
    h.write(
        "create_workflow",
        value=TOTAL,
        **h.create_kwargs(LATE_WF, 2, 2, 2),
    )
    time.sleep(10)
    h.write("settle_workflow", account=h.stranger, workflow_id=LATE_WF)
    late = h.read("get_workflow", workflow_id=LATE_WF)
    h.note(
        "settle without claim refunds every pending stage",
        late.get("status") == "closed"
        and int(late.get("refunded_amount", 0)) == TOTAL
        and int(late.get("remaining_amount", 0)) == 0,
        f"status={late.get('status')} refunded={late.get('refunded_amount')}",
    )
    h.guard(
        "double settle",
        "get_workflow",
        {"workflow_id": LATE_WF},
        "settle_workflow",
        {"workflow_id": LATE_WF},
        account=h.stranger,
    )

    print(f"\nContract: {h.addr}")
    print(f"Result: {h.passed} passed, {h.failed} failed")
    return 0 if h.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
