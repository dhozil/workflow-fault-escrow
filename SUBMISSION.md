# Submission: WorkflowFaultEscrow

## Category
Intelligent Contracts

## Contract Name
WorkflowFaultEscrow

## Summary
A multi-agent workflow escrow. The client funds one tranche per stage and fixes each stage's
acceptance spec up front; each stage's worker commits the exact artifact it produced
(URL + SHA-256). The client either accepts a stage (paid immediately) or files one
hash-bound failure claim naming a submitted stage. GenLayer validators fetch the named
stage's artifact and the client's report, verify both hashes, and answer exactly one
categorical question — is this stage at fault? The escrow settles from that category alone.

## What Makes This Unique
- **Categorical, exactly-bound consensus**: the only consequential consensus field is a
  verdict category (`stage_at_fault` / `stage_not_at_fault`) that validators must match
  exactly; there is no score, percentage or tolerance that could shift a payout
- **Fixed settlement function**: the verdict maps to one deterministic payout matrix
  (blamed stage refunded, other submitted stages paid, never-submitted refunded), so an
  accepted verdict always produces the same distribution
- **Both sides bring authenticated evidence**: the worker commits the artifact, the client
  commits the failure report; validators fetch both contract-side and verify each against
  its committed hash before any judgment
- **Correct burden of proof, including the sharp edge**: an unverifiable *report* means the
  claim is unsupported, while an unverifiable *artifact* counts against the worker — only
  the worker controls that URL, so evidence withholding cannot defeat a claim
- **No premature refunds**: a submitted stage can only be refunded by a recorded
  `stage_at_fault` verdict or by the neutral time gate
- **Bounded lifecycle**: submission deadline, failure window, settlement grace and a 7-day
  claim lapse; every path reaches a terminal state
- **Atomic accounting**: `released + refunded == total`; accepted/paid/refunded stages are
  terminal and settlement skips them, so nothing can be paid twice
- **Verification ids**: `terms_binding`, `artifact_binding`, `failure_binding`,
  `verdict_binding`

## How Consensus Is Used
`resolve_failure` runs `gl.vm.run_nondet_unsafe(judge, validator_fn)`.

- `judge` fetches the named stage artifact and the client's report, verifies each against its
  committed SHA-256, then calls `gl.nondet.exec_prompt(...)` with the fetched **content**
  (spec + artifact + report) and `response_format="json"`. Acquisition failures produce a
  deterministic verdict with reasoning instead of a revert: an unverifiable artifact is
  `stage_at_fault`, an unverifiable report is `stage_not_at_fault`.
- `validator_fn` requires a `Return` carrying one of the two allowed categories and a
  non-empty reasoning string, re-runs the same judge independently, and accepts only an
  **exactly** equal verdict category.
- The contract snapshots the workflow/stage into plain values before consensus (no storage
  reads inside nondet) and applies the settlement afterwards in deterministic code.

## Technical Details
- Python GenLayer Intelligent Contract, pinned runner
  `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`
- Storage: `Workflow` and `Stage` dataclasses under `TreeMap[str, ...]`, plus an
  `artifact_usage` index; `u256` amounts/counters, `i256` failing index; ISO time gates
- 2-6 stages per workflow, distinct non-zero workers (client excluded), one spec per stage,
  `total_amount == sum(tranches)`, combined windows ≤ 90 days, 300 KB evidence cap
- Methods: 9 (6 write, 3 view), no constructor params
- `genvm-lint check`: passed

## Use Case
Agent pipelines and multi-stage outsourced work where one stage fails and someone must bear
the loss: LLM agent chains, ETL/data contracts, sub-contracted delivery, creative production
chains, research or analysis pipelines.

## Security Review
Full report: `AUDIT.md`. The contract was designed with the failure classes from earlier
submissions already closed (contract-side evidence fetching, exact binding of the
consequential field, no lock without a time gate, no premature refund, SSRF and input caps,
terminal states). Its own audit found and fixed:

| ID | Severity | Issue | Fix |
|---|---|---|---|
| H-1 | High | Evidence withholding could defeat a legitimate claim | Unverifiable/tampered artifact counts as `stage_at_fault` (worker controls that URL) |
| M-1 | Medium | Settlement could preempt the client's failure window | Settle at `deadline + failure_window + grace`, combined window capped |
| M-2 | Medium | Duplicate worker addresses made attribution ambiguous | Distinct non-zero workers, client excluded |
| M-3 | Medium | One artifact could be committed for two stages | Per-workflow `artifact_usage` index |
| M-4/M-5 | Low | Guard/message and replay ordering | Closed guard first; one claim per workflow |

## Verification
- 12/12 direct-mode tests (`gltest tests/ -q`)
- Deterministic on-chain verification on studionet (`scripts/verify_onchain.py`): **23/23 PASS**
- Full AI-consensus attribution on studionet, evidence pinned to a git commit and fetched by
  the contract itself:
  - `stage_at_fault`: workflow `consensus-atfault-1794999399`, resolve tx
    `0xf261b7e2fffe7b14e3327bd09a61c33f8767695b413d58325face0375c8343f4`,
    3 validators `agree`, blamed stage refunded 600, other stage paid 400
  - `stage_not_at_fault`: workflow `consensus-nofault-1794999470`, resolve tx
    `0x4ab6d4da55cd4151177572ac2b29e344b16ee33929b8720909a242799c15ac06`,
    3 validators `agree`, all 1000 released to the workers
  - both runs: empty `stderr`, `released + refunded == total`, workflow closed
- On-chain code byte-identical to `contracts/workflow_fault_escrow.py`

## Live Deployment
Deployed on GenLayer studionet (chain `61999`):
[`0xb7Ce6bBc39DFC754a2BD35ADBFC4CA9a95e3e3F1`](https://explorer-studio.genlayer.com/address/0xb7Ce6bBc39DFC754a2BD35ADBFC4CA9a95e3e3F1)
Deploy tx: `0x8ec0f4fa9853287eaedb91ba0eb1f02eb72c3ab2cd3e09a622d57f31e57b78ff`

## Source Code
See `contracts/workflow_fault_escrow.py`
