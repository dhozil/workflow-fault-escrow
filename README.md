<div align="center">

# WorkflowFaultEscrow

**Multi-agent workflow escrow where a disputed stage is settled by one evidence-bound categorical verdict: was this stage at fault? The payout is a pure function of that answer.**

![GenLayer](https://img.shields.io/badge/GenLayer-Intelligent%20Contract-6a4cff)
![Python](https://img.shields.io/badge/Python-3.12-3776AB)
![Status](https://img.shields.io/badge/status-live%20on%20studionet-2ea44f)
![Tests](https://img.shields.io/badge/tests-12%20passed-2ea44f)
![On-chain](https://img.shields.io/badge/on--chain%20checks-23%20passed-2ea44f)

[Live Contract](https://explorer-studio.genlayer.com/address/0xb7Ce6bBc39DFC754a2BD35ADBFC4CA9a95e3e3F1) ·
[Security Audit](AUDIT.md) ·
[GenLayer Docs](https://docs.genlayer.com)

</div>

---

## Why

Agent pipelines fail in the middle, and the only party holding the money decides who
eats the loss. Orchestrators stall payment across the whole pipeline because one stage
misbehaved; good upstream work goes unpaid; a worker accused of a bad stage has no
neutral record to point at.

WorkflowFaultEscrow turns that argument into a fixed, auditable decision:

- the client escrows **one tranche per stage** and writes each stage's acceptance spec up front;
- each stage's worker commits the **exact artifact** it produced (URL + SHA-256);
- the client either accepts a stage (paid immediately) or files **one** hash-bound failure
  claim naming a single submitted stage;
- validators fetch the named stage's artifact and the client's report and answer exactly
  one question: **is this stage at fault?**;
- the escrow settles from that category alone: the blamed stage is refunded, every other
  submitted stage is paid, never-submitted stages are refunded.

Reference: [GenLayer Use Cases — Agent-to-agent commitments](https://docs.genlayer.com/understand-genlayer-protocol/typical-use-cases)
("which participant caused a multi-agent workflow to fail").

## Live Deployment

| | |
|---|---|
| **Contract** | [WorkflowFaultEscrow](https://explorer-studio.genlayer.com/address/0xb7Ce6bBc39DFC754a2BD35ADBFC4CA9a95e3e3F1) |
| **Address** | `0xb7Ce6bBc39DFC754a2BD35ADBFC4CA9a95e3e3F1` |
| **Network** | GenLayer studionet (chain `61999`) |
| **Deploy tx** | `0x8ec0f4fa9853287eaedb91ba0eb1f02eb72c3ab2cd3e09a622d57f31e57b78ff` |
| **Constructor args** | none |
| **On-chain code** | byte-identical to `contracts/workflow_fault_escrow.py` (SHA-256 `2c80ae5f…08fe`) |

## Highlights

- 🧷 **One categorical verdict, no free-form split** — validators must return
  `stage_at_fault` or `stage_not_at_fault` and agree on it **exactly**; there is no
  percentage, score or tolerance that one node could bend into a different payout
- 🔍 **Both sides bring their own authenticated evidence** — the worker commits the stage
  artifact, the client commits the failure report; validators fetch both and verify each
  against its committed hash before judging
- ⚖️ **Fixed settlement function** — the verdict maps to one deterministic payout matrix,
  so consensus cannot produce a partially different distribution
- 🧾 **Claimant bears the burden, with the right exception** — an unverifiable *report* means
  the claim is unsupported; an unverifiable *artifact* counts against the worker, because
  only the worker controls that URL (evidence withholding cannot defeat a claim)
- 🛡️ **No premature refunds** — the client cannot touch a submitted stage without a recorded
  `stage_at_fault` verdict
- ⏱️ **Bounded on every path** — submission deadline, failure window, settlement grace, and a
  7-day lapse for unresolved claims; the escrow always reaches a terminal state
- 🧮 **Atomic accounting** — `released + refunded == total`; accepted/paid/refunded stages are
  terminal and settlement never touches them, so nothing can be paid twice
- 🧱 **Reusable primitive** — agent pipelines, outsourced multi-stage projects, ETL/data
  contracts, creative production chains, sub-contracted delivery

## How It Works

```
create_workflow (payable; client, workers[], amounts[], specs[], windows)
      |  returns terms_binding
      v
submit_stage (worker i only; artifact URL + sha256 -> artifact_binding)
      |  one artifact cannot be used for two stages
      +--> accept_stage (client)  -> that tranche paid immediately
      |
      v
file_failure (client, once, targets one submitted stage + hash-bound report)
      |  freezes the workflow: no new submissions, no accepts
      v
resolve_failure (permissionless crank; validators fetch artifact + report)
      |                         |
  stage_at_fault          stage_not_at_fault
      |                         |
  blamed stage refunded      all submitted stages paid
  other submitted paid       never-submitted refunded
      +-----------+-------------+
                  v
          claim lapsed / no claim after deadline + failure window + grace
                  -> submitted stages paid, never-submitted refunded
```

## Settlement Matrix

Every stage ends in exactly one of `accepted`, `paid`, `refunded`.

| Trigger | Named stage | Other submitted stages | Never-submitted stages |
|---|---|---|---|
| `accept_stage` by client | paid immediately (terminal) | untouched | untouched |
| `resolve_failure` → `stage_at_fault` | **refunded to client** | paid to their workers | refunded to client |
| `resolve_failure` → `stage_not_at_fault` | paid | paid | refunded |
| `settle_workflow`, no claim | paid | paid | refunded |
| `settle_workflow`, claim lapsed (7d) | paid | paid | refunded |

## Methods

| Role | Method | Guard |
|---|---|---|
| Client (funder) | `create_workflow` | payable; `value == sum(amounts)`; 2-6 distinct non-zero workers; client cannot be a worker; one spec per stage; combined windows ≤ 90 days |
| Stage worker | `submit_stage` | exactly stage `i`'s worker; before `submit_deadline`; stage `pending`; artifact URL + 64-hex SHA-256; no artifact reuse in the workflow |
| Client | `accept_stage` | client-only; stage `submitted`; pays that tranche immediately |
| Client | `file_failure` | client-only; one claim per workflow; target must be `submitted`; before `deadline + failure_window`; hash-bound report |
| Any (crank) | `resolve_failure` | workflow `claimed`; consensus over the fetched artifact + report |
| Any (crank) | `settle_workflow` | after the time gates, or after a claim lapses |
| Any | `get_workflow`, `get_stage`, `get_workflow_stages` | read-only |

## Consensus Design

```
judge:
  fetch the named stage artifact -> verify sha256 against the commitment
    unreachable / mismatch  -> stage_at_fault  (the worker controls this URL)
  fetch the client report -> verify sha256 against the commitment
    unreachable / mismatch  -> stage_not_at_fault (the claimant controls this URL)
  exec_prompt(prompt with spec + artifact + report, response_format="json")
    verdict must be one of the two categories, otherwise the claim is unsupported

validator_fn:
  require leader Return + dict + an allowed verdict category + non-empty string reasoning
  re-run the whole judge path independently
  accept only if the verdict category is EXACTLY equal
```

Why this shape:

- **The category *is* the settlement.** There is no secondary consumed field, so a
  validator cannot accept a different distribution by agreeing on a label.
- **Both evidence items are hash-bound and fetched contract-side.** No claimant-supplied
  "verified" snapshot, no URLs passed to the model instead of content.
- **Deterministic defaults instead of reverts.** Missing or tampered evidence produces a
  recorded verdict with reasoning, so the escrow always settles and the outcome is auditable.
- **Reasoning is audit-only.** It is required to be a non-empty string and stored, but no
  later computation reads it.
- **No storage reads inside nondet.** The workflow and stage are snapshotted into plain
  values before consensus; payouts run afterwards in deterministic code.

## Security Controls

Carried over from prior audit findings on this portfolio (see [`AUDIT.md`](AUDIT.md)):

- evidence fetched and hash-verified contract-side; per-item bindings returned and stored
- exact binding of the only consequential consensus field (the verdict category)
- per-workflow artifact-reuse index and one-shot submissions/claims
- SSRF-hardened URL validation (public dotted hosts; no credentials, IPv6 literals,
  localhost, private or link-local/metadata ranges)
- pre-parse size caps on every JSON argument and on fetched artifacts
- zero-address rejection, workflow-id allowlist, 2-6 stage bounds
- all state transitions written before any external transfer
- `terms_binding` / `artifact_binding` / `failure_binding` / `verdict_binding` per call

## Limitations (read before real money)

- **Client silence is acceptance.** A submitted stage that the client neither accepts nor
  disputes is paid at settlement. The client has the whole failure window to act.
- **Claims lapse after 7 days** if nobody (including the client) resolves them. Any account
  can crank `resolve_failure`; the lapse is a lock-safety valve, not a preferred path.
- **Artifact availability is the worker's responsibility.** Because the worker controls the
  artifact URL, an artifact that no longer resolves as committed counts against that stage.
  Host artifacts on reliable, commit-pinned storage.
- **Specs are client-authored.** A client can write a spec that is impossible to satisfy;
  workers should read the spec before submitting. Fault is judged against the spec, not
  against industry reasonableness.
- **The artifact is the record, not proof of authorship.** The contract cannot prove the
  bytes were produced by the declared agent, only that they are the bytes the agent committed.
- **Upstream/downstream propagation is out of scope.** Fault is attributed to the named
  stage's own deliverable against its own spec; blame the upstream stage explicitly instead.
- **Not a court.** This is an agreed, evidence-based settlement mechanism for agent workflows.

## Usage Sketch

```text
# 1. client funds all stage tranches and fixes the specs
create_workflow(
  workflow_id="etl-42",
  workers_json='["0xWorkerA...", "0xWorkerB..."]',
  amounts_json="[600, 400]",
  stage_specs_json='["Deliver a JSON schema with keys service and port.", "Deliver the migration that uses those keys."]',
  submit_window_seconds=2592000,
  failure_window_seconds=259200,
  settlement_grace_seconds=604800,
)  # value = 1000, returns terms_binding

# 2. each worker commits the exact artifact it produced
submit_stage(workflow_id="etl-42", index=0,
             artifact_url="https://raw.githubusercontent.com/org/repo/<commit>/schema.json",
             artifact_sha256="<64 hex>")   # returns artifact_binding

# 3a. happy path
accept_stage(workflow_id="etl-42", index=0)

# 3b. or one disputed stage
file_failure(workflow_id="etl-42", failing_index=0,
             report_url="https://raw.githubusercontent.com/org/repo/<commit>/report.md",
             report_sha256="<64 hex>")     # returns failure_binding
resolve_failure(workflow_id="etl-42")      # returns verdict + verdict_binding

# 4. lock safety valve
settle_workflow(workflow_id="etl-42")
```

## Test

```bash
gltest tests/ -q      # 12 passed (direct mode)
```

Coverage: creation/funding/worker-set validation, worker-only deadline-bounded submission,
artifact binding and reuse, accept-flow and its guards, failure-claim guards and binding,
fault / no-fault settlements, withheld or substituted artifact favours the claim while an
unverifiable report does not, unusable model output, legacy boolean compatibility,
validator exact-verdict enforcement, settlement without a claim, claim lapse, and a mixed
accept + fault settlement with the accounting invariant.

## Lint

```bash
genvm-lint check contracts/workflow_fault_escrow.py
```

`check` passes: 9 methods (3 view, 6 write), 0 constructor params.

## On-Chain Verification

Deterministic studionet run (`leader_only=True`), script
[`scripts/verify_onchain.py`](scripts/verify_onchain.py) — **23/23 PASS**:

| Step | Result |
|---|---|
| Funding below stage total | reverted (expected) |
| `create_workflow` funded (1000 wei, 2 stages) | accepted, `status=open` |
| Stage tranches/workers stored | `[600, 400]`, workers in order |
| Zero-address worker / duplicate worker / client as worker | reverted (expected) |
| Duplicate workflow id | reverted (expected) |
| Stranger submits a stage / other worker submits this stage | reverted (expected) |
| Worker submits hash-bound artifact | accepted; binding matches `sha256(wf\|index\|url\|sha)` |
| Double submission / artifact reused / private host | reverted (expected) |
| Stranger accepts a stage | reverted (expected) |
| Client accepts a stage | accepted, `released=600` |
| Double accept / claim against a pending stage | reverted (expected) |
| Stranger files a failure | reverted (expected) |
| Client files hash-bound failure claim | accepted, `status=claimed`, `failing_index=1` |
| Double claim / settle while claim resolvable | reverted (expected) |
| `settle_workflow` after the time gates | accepted, `status=closed`, refunded `1000`, remaining `0` |
| Double settle | reverted (expected) |

### Full AI consensus (real validators, not leader-only)

Evidence is pinned to commit
[`e5ae50d`](https://github.com/dhozil/workflow-fault-escrow/tree/e5ae50d9c3f32bd4973ef581e416e13a8343a93b/evidence);
the contract fetched each file itself and verified the committed SHA-256 before judging.

**Case A — a real failure is confirmed (`stage_at_fault`).** Stage 0 committed a prose
meeting note; the client's report said it lacked the required JSON block and endpoint table.
Validators: 3 `agree`, `stderr` empty.

| | |
|---|---|
| Workflow | `consensus-atfault-1794999399` (2 stages, 600 + 400) |
| `file_failure` tx | `0x9d0dd8e0065300a587991ae67f661c6e793de4083760d7d064232b56e737ff62` |
| `resolve_failure` tx | `0xf261b7e2fffe7b14e3327bd09a61c33f8767695b413d58325face0375c8343f4` |
| Verdict | `stage_at_fault` — *"Artifact shows only a meeting summary with no JSON block (service/port keys) and no markdown endpoint table; spec explicitly requires both."* |
| Settlement | stage 0 `refunded` (600), stage 1 `paid` (400), `released=400`, `refunded=600`, `remaining=0`, workflow `closed` |
| `verdict_binding` | `0db98dfbbd2a6edaa159c5519573f9dbddaeb02896d10fe52909f5ccb7de607a` |

**Case B — an unfounded claim is rejected (`stage_not_at_fault`).** Stage 0 committed the
compliant artifact; the client's report claimed the required payload was missing.
Validators: 3 `agree`, `stderr` empty.

| | |
|---|---|
| Workflow | `consensus-nofault-1794999470` (2 stages, 600 + 400) |
| `file_failure` tx | `0xf6b3ba001c1731c5eef10ecb52f48462e16cb6798dafb57f639b2c4cc80b48ad` |
| `resolve_failure` tx | `0x4ab6d4da55cd4151177572ac2b29e344b16ee33929b8720909a242799c15ac06` |
| Verdict | `stage_not_at_fault` — *"…The artifact clearly contains both the required JSON block (`{"service": "checkout-api", "port": 8080}`) and a markdown table with three HTTP endpoints. The client failure report contradicts the actual artifact contents and is therefore unfounded."* |
| Settlement | stage 0 `paid` (600), stage 1 `paid` (400), `released=1000`, `refunded=0`, `remaining=0`, workflow `closed` |
| `verdict_binding` | `4518354d38e4d5f0974a166e82d37d93fbefdfb767316b966878b132c9fd467b` |

The two cases differ only in *which* artifact was committed to stage 0 and which report was
filed, and the escrow moved in exactly opposite directions — the whole disputed amount is
decided by the one categorical field validators must agree on.
