# Security Audit — WorkflowFaultEscrow

**Scope:** `contracts/workflow_fault_escrow.py` (studionet deployment
`0xb7Ce6bBc39DFC754a2BD35ADBFC4CA9a95e3e3F1`), its lifecycle, consensus path and every
public entry point.
**Method:** adversarial review against the failure classes found in this portfolio's
earlier audits, targeted regression tests, deterministic on-chain verification, and a
full AI-consensus attribution run on studionet.

This contract was written *after* the audits of six earlier submissions, so the first
section is the set of controls that were designed in from the start. Section 2 lists the
issues found by this contract's own audit.

---

## 1. Controls designed in from earlier audits

| Prior finding class | Control in this contract |
|---|---|
| Fetched content discarded / URLs passed to the model instead of evidence | `judge` fetches the artifact and the report and embeds the **fetched text** in the prompt |
| Claimant-supplied hash verified against claimant-supplied snapshot | both evidence items are verified against **contract-side fetched bytes** |
| Validators accepting different raw values that change the payout | the only consequential field is the verdict **category**, compared exactly; no scores, percentages or tolerances |
| Unverified descriptive text reused later | `reasoning` is required to be a non-empty string, stored, and **never read again** |
| Funded escrow locked when adjudication stays inconclusive | `settle_workflow`: time-based settlement for no-claim and a 7-day lapse for unresolved claims |
| Premature refund without a recorded adverse outcome | a submitted stage can only be refunded by a recorded `stage_at_fault` verdict (or by the neutral time gate) |
| Anyone can resolve someone else's commitment without authority | permissionless calls are cranks only; workers submit only their stage; the client is the only one who accepts or files |
| One piece of evidence paying several tranches | per-workflow `artifact_usage` index; one-shot submissions |
| Double submit / double pay | terminal stage states (`accepted`/`paid`/`refunded`), one claim per workflow, `closed` guard, `released + refunded == total` |
| SSRF / private-host fetch | public dotted host only; credentials, IPv6 literals, localhost, `.local`/`.internal`, `10/8`, `127/8`, `192.168/16`, `172.16/12`, `169.254/16` rejected (submission **and** fetch) |
| Pre-parse input bombs | length caps before every `json.loads` and on fetched artifacts (300 KB) |
| Zero-address payout / key confusion | zero address rejected; workflow id allowlist without `#` |
| External transfer before final state write | `_settle` / `accept_stage` finish all state writes, then emit transfers |
| No verification id per call | `terms_binding`, `artifact_binding`, `failure_binding`, `verdict_binding` |
| Prohibited float division | no `/` anywhere; integer arithmetic only |
| Sealed commit receiving plaintext | N/A — commitments here are over **public** evidence for integrity, not secrecy |

---

## 2. Findings from this contract's own audit

| ID | Severity | Issue | Status |
|---|---|---|---|
| H-1 | **High** | Evidence withholding: a worker could commit an artifact, take the URL down (or serve different bytes), and defeat a legitimate failure claim, because any verification failure was treated as "claim unsupported" | **Fixed** — an artifact that cannot be fetched or no longer matches its committed hash now counts as `stage_at_fault`, since only the worker controls that URL |
| M-1 | Medium | Settlement could open before the client's failure window closed (`deadline + grace`), letting a crank preempt a legitimate claim | **Fixed** — settlement opens at `deadline + failure_window + settlement_grace`, the combined window is capped at 90 days, and a submitted stage is never refunded by a crank alone |
| M-2 | Medium | Duplicate worker addresses would make fault attribution ambiguous (one address owning two stages) | **Fixed** — workers must be distinct, and the client cannot be a worker |
| M-3 | Medium | The same artifact could be committed for two stages (one deliverable claimed twice) | **Fixed** — per-workflow `artifact_usage` index |
| M-4 | Low | `resolve_failure` reported "no active claim" instead of "closed" for a settled workflow | **Fixed** — explicit closed guard first |
| M-5 | Low | Duplicate/already-submitted claims could be replayed across statuses | **Fixed** — one claim per workflow, target must be `submitted`, and filing freezes the workflow |
| L-1 | Low | An unresolved claim lapses after 7 days and the stages are paid | **Accepted, documented** — the claimant bears the burden; anyone can crank resolution |
| L-2 | Low | Client silence on a submitted stage is acceptance at settlement | **Accepted, documented** — the failure window is the client's decision period |
| L-3 | Low | A client can author an unsatisfiable spec and then claim failure | **Accepted, documented** — the spec is public before submission; workers should read it |
| L-4 | Low | A host outage on a worker's artifact URL counts against that stage | **Accepted, documented** — the worker owns the availability of its evidence; use pinned/reliable storage |
| L-5 | Low | Artifact bytes do not prove authorship or freshness | **Accepted, documented** — the contract binds *which* bytes were judged |
| L-6 | Low | Upstream/downstream failure propagation is not modelled | **Accepted, documented** — the client must name the stage whose own deliverable failed |

### H-1 detail

Original behaviour:

```
artifact unreachable or hash mismatch  -> stage_not_at_fault  (worker paid)
report unreachable or hash mismatch    -> stage_not_at_fault  (claim unsupported)
```

Symmetry looked fair but it was exploitable: the worker chooses the artifact URL, so a
worker facing a good claim could simply remove or mutate the artifact and collect the
tranche. The fix keeps the burden of proof on the claimant **only for evidence the
claimant controls**:

| Evidence | Controller | Failure treatment |
|---|---|---|
| Stage artifact | worker | `stage_at_fault` (withholding cannot defeat a claim) |
| Failure report | client | `stage_not_at_fault` (an unsupported claim does not move money) |

Regression tests: `test_resolve_withheld_artifact_favours_the_claim` covers unreachable
artifact, substituted artifact bytes and a tampered report in one flow.

---

## 3. Double-submit / replay matrix

| Attempt | Result |
|---|---|
| `create_workflow` with an existing id | revert `Workflow already exists` |
| `submit_stage` twice on one stage | revert `Stage artifact was already submitted` |
| `submit_stage` reusing an artifact in the workflow | revert `Artifact already used for another stage in this workflow` |
| `submit_stage` by a non-owner worker | revert `Only the stage worker can submit its artifact` |
| `submit_stage` after the deadline | revert `Submission deadline has passed` |
| `accept_stage` twice / on an accepted stage | revert `Stage is not awaiting a decision` |
| `accept_stage` after a claim froze the workflow | revert `Workflow is not open` |
| `file_failure` twice | revert `Workflow is not open` |
| `file_failure` past the failure window | revert `Failure window has passed` |
| `file_failure` on a pending/accepted stage | revert `Failure can only target a submitted stage` |
| `resolve_failure` twice | second call reverts (`Workflow is closed`) |
| `settle_workflow` on a closed workflow | revert `Workflow is closed` |
| `settle_workflow` while a claim is resolvable | revert `Failure claim is still within its resolution window` |
| Double payout for one stage | impossible: `accepted`/`paid`/`refunded` are terminal and settlement skips them |

---

## 4. Caller / authorization matrix

| Actor | Can | Cannot |
|---|---|---|
| Client (funder) | `create_workflow`; `accept_stage`; one `file_failure`; cranks | submit a stage; refund a submitted stage without a recorded fault verdict; change specs/amounts; accept after a claim |
| Stage worker `i` | `submit_stage(i)` once before the deadline; cranks | submit another stage; accept; file a failure; get paid twice |
| Stranger | `resolve_failure`, `settle_workflow` (time-gated cranks) | submit, accept, claim, or influence the verdict |
| Anyone | views | write state |

The client is always `gl.message.sender_address`; the worker is fixed per stage at creation,
so no caller can act in another party's name or redirect a payout.

---

## 5. Consensus-path audit

**Consequential-field budget.** `terms_binding` (specs, workers, amounts, windows),
`artifact_binding` (exact artifact), `failure_binding` (exact claim), and the **verdict
category**. Everything else is audit data.

**Validator obligations.** `validator_fn` requires the leader result to be a `Return` with
one of the two verdict categories and a non-empty string reasoning, then re-runs `judge`
and requires **exact** verdict equality. There is no tolerance, so two accepted runs cannot
settle differently.

**What consensus cannot do.** The model cannot choose a recipient, an amount, a split, or a
new state. It selects a category; `_settle` maps that category deterministically.

**Failure handling.**

| Failure | Behaviour |
|---|---|
| Artifact unreachable / hash mismatch | recorded `stage_at_fault` (worker controls this evidence) |
| Report unreachable / hash mismatch | recorded `stage_not_at_fault` (unsupported claim) |
| Model returns an unknown category | recorded `stage_not_at_fault` with "no usable verdict category" |
| Model/leader raises | leader result is an error, validator returns `False`, consensus rotates |
| Validators disagree | rotation; no state change |

**Prompt injection.** Spec, artifact and report are delimited and labelled untrusted; the
model is told the artifact is the authoritative record and that assertions in the report are
not evidence. The only output consumed is a category from a fixed set.

---

## 6. Staff feedback mapping (summary)

| Feedback theme | Verdict |
|---|---|
| Validators must independently verify the reason/field that determines the outcome | **Applied** — one categorical field, exact agreement, re-derived from fetched evidence |
| Fetch evidence contract-side; bind to what was actually fetched | **Applied** — both evidence items are fetched and hash-verified before judging |
| Each party submits its own authenticated case | **Applied** — worker artifact, client report |
| Winner settles atomically | **Applied** — settlement runs in the resolving transaction |
| No permanent locks on inconclusive adjudication | **Applied** — time-based settlement plus a 7-day claim lapse |
| No premature refunds; record the adverse outcome first | **Applied** — refund only after a recorded `stage_at_fault` |
| Restrict resolution to authorized parties / enforce timing | **Applied** — cranks cannot choose outcomes; all windows enforced |
| Bind every field reused downstream | **Applied** — reasoning is stored but never reused |
| No prohibited float division | **Compliant** — integer arithmetic only |
| Returned verification ids; resolvable dependency pin | **Compliant** — four bindings; pinned `py-genlayer:1jb45aa8…` |
| Sealed commit must not receive plaintext | **N/A** — no sealed bids; hashes are integrity commitments over public evidence |
| Precedent / fee / performance-analysis items | **N/A** — those mechanisms are not part of this contract |

---

## 7. Verification evidence

| Check | Result |
|---|---|
| `genvm-lint check` | pass — 9 methods (3 view, 6 write) |
| Direct-mode tests | **12/12 passed** |
| Deterministic on-chain (`leader_only=True`) | **23/23 PASS** |
| Full AI consensus attribution | see README — both `stage_at_fault` and `stage_not_at_fault` cases resolved on studionet |
| Source ↔ deployment | on-chain code compared byte-for-byte with the repository source |
| Deployment | `0xb7Ce6bBc39DFC754a2BD35ADBFC4CA9a95e3e3F1`, deploy tx `0x8ec0f4fa…78ff` |
