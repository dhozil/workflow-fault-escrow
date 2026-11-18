"""Direct-mode tests for the WorkflowFaultEscrow contract."""

import hashlib
import json
import re
from datetime import datetime, timedelta

from gltest.direct import create_address

WF_ID = "pipeline-1"
CLIENT_ADDR = create_address("wfe-client")
WORKER_A_ADDR = create_address("wfe-worker-a")
WORKER_B_ADDR = create_address("wfe-worker-b")
STRANGER_ADDR = create_address("wfe-stranger")
CLIENT = "0x" + CLIENT_ADDR.hex()
WORKER_A = "0x" + WORKER_A_ADDR.hex()
WORKER_B = "0x" + WORKER_B_ADDR.hex()

AMOUNTS = [600, 400]
TOTAL = sum(AMOUNTS)
SPECS = [
    "Stage 1 must return a JSON schema with fields id and items.",
    "Stage 2 must return a migration file that references stage 1 fields.",
]
ARTIFACT_A = '{"id": "schema-v1", "items": []}'
ARTIFACT_B = "ALTER TABLE items ADD COLUMN id TEXT;"
REPORT = "Stage 1 schema omits the required items array."

ARTIFACT_A_URL = "https://artifacts.example.com/stage-1.json"
ARTIFACT_B_URL = "https://artifacts.example.com/stage-2.sql"
REPORT_URL = "https://reports.example.com/failure.txt"

FAULT_JSON = json.dumps(
    {"verdict": "stage_at_fault", "reasoning": "The artifact has no items array."}
)
NO_FAULT_JSON = json.dumps(
    {"verdict": "stage_not_at_fault", "reasoning": "The artifact satisfies the spec."}
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _create(
    direct_vm,
    contract,
    workflow_id=WF_ID,
    workers=None,
    amounts=None,
    specs=None,
    submit_window=3600,
    failure_window=600,
    grace=600,
    value=None,
):
    workers = [WORKER_A, WORKER_B] if workers is None else workers
    amounts = AMOUNTS if amounts is None else amounts
    specs = SPECS if specs is None else specs
    if value is None:
        value = sum(amounts)
    direct_vm.sender = CLIENT_ADDR
    direct_vm.value = value
    return contract.create_workflow(
        workflow_id=workflow_id,
        workers_json=json.dumps(workers),
        amounts_json=json.dumps(amounts),
        stage_specs_json=json.dumps(specs),
        submit_window_seconds=submit_window,
        failure_window_seconds=failure_window,
        settlement_grace_seconds=grace,
    )


def _submit(direct_vm, contract, index, worker=None, url=None, body=None, workflow_id=WF_ID):
    default_body = ARTIFACT_A if index == 0 else ARTIFACT_B
    default_url = ARTIFACT_A_URL if index == 0 else ARTIFACT_B_URL
    body = default_body if body is None else body
    direct_vm.sender = WORKER_A_ADDR if index == 0 else WORKER_B_ADDR
    if worker is not None:
        direct_vm.sender = worker
    return contract.submit_stage(
        workflow_id=workflow_id,
        index=index,
        artifact_url=default_url if url is None else url,
        artifact_sha256=_sha(body),
    )


def _file_failure(direct_vm, contract, index=0, report=None, url=None, workflow_id=WF_ID):
    report = REPORT if report is None else report
    direct_vm.sender = CLIENT_ADDR
    return contract.file_failure(
        workflow_id=workflow_id,
        failing_index=index,
        report_url=REPORT_URL if url is None else url,
        report_sha256=_sha(report),
    )


def _mock_evidence(direct_vm, artifact=ARTIFACT_A, report=REPORT):
    direct_vm.mock_web(re.escape(ARTIFACT_A_URL), {"status": 200, "body": artifact})
    direct_vm.mock_web(re.escape(ARTIFACT_B_URL), {"status": 200, "body": ARTIFACT_B})
    direct_vm.mock_web(re.escape(REPORT_URL), {"status": 200, "body": report})


def _mock_verdict(direct_vm, payload):
    direct_vm.mock_llm(r"adjudicating one disputed stage", payload)


def test_create_workflow_validates_and_funds(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    direct_vm.check_pickling = True
    direct_vm.sender = CLIENT_ADDR
    direct_vm.value = TOTAL

    with direct_vm.expect_revert("Workflow id is required"):
        _create(direct_vm, contract, workflow_id="")
    with direct_vm.expect_revert(
        "Workflow id may only contain letters, digits, dash, underscore or dot"
    ):
        _create(direct_vm, contract, workflow_id="bad id#1")
    with direct_vm.expect_revert("Workflow must have between 2 and 6 stages"):
        _create(direct_vm, contract, workers=[WORKER_A], amounts=[TOTAL], specs=[SPECS[0]])
    with direct_vm.expect_revert("Workers, amounts and specs must have equal length"):
        _create(direct_vm, contract, amounts=[TOTAL], specs=[SPECS[0], SPECS[1]])
    with direct_vm.expect_revert("Stage workers must be distinct"):
        _create(direct_vm, contract, workers=[WORKER_A, WORKER_A])
    with direct_vm.expect_revert("Client cannot be a stage worker"):
        _create(direct_vm, contract, workers=[CLIENT, WORKER_B])
    with direct_vm.expect_revert("Address must not be the zero address"):
        _create(
            direct_vm,
            contract,
            workers=["0x0000000000000000000000000000000000000000", WORKER_B],
        )
    with direct_vm.expect_revert("Invalid Ethereum address"):
        _create(direct_vm, contract, workers=["0x123", WORKER_B])
    with direct_vm.expect_revert("Stage amounts must be > 0"):
        _create(direct_vm, contract, amounts=[0, TOTAL], value=TOTAL)
    with direct_vm.expect_revert("Stage amounts must be integers"):
        _create(direct_vm, contract, amounts=["600", 400], value=TOTAL)
    with direct_vm.expect_revert("Stage specs are required"):
        _create(direct_vm, contract, specs=["   ", SPECS[1]])
    with direct_vm.expect_revert("Submission window must be at least 1 second"):
        _create(direct_vm, contract, submit_window=0)
    with direct_vm.expect_revert("Submission window cannot exceed 90 days"):
        _create(direct_vm, contract, submit_window=7776001)
    with direct_vm.expect_revert("Failure window must be at least 1 second"):
        _create(direct_vm, contract, failure_window=0)
    with direct_vm.expect_revert("Settlement grace must be at least 1 second"):
        _create(direct_vm, contract, grace=0)
    with direct_vm.expect_revert("Combined windows cannot exceed 90 days"):
        _create(direct_vm, contract, submit_window=7776000, failure_window=2, grace=2)
    with direct_vm.expect_revert("Workers payload is too large"):
        _create_oversized_workers(direct_vm, contract, TOTAL)
    with direct_vm.expect_revert("Amounts payload is too large"):
        _create_oversized_amounts(direct_vm, contract, TOTAL)
    with direct_vm.expect_revert("Workers must be valid JSON"):
        _create_bad_json(direct_vm, contract, TOTAL)
    with direct_vm.expect_revert("Escrow funding must be > 0"):
        _create(direct_vm, contract, value=0)
    with direct_vm.expect_revert("Escrow funding must equal the stage total"):
        _create(direct_vm, contract, value=TOTAL - 1)

    created = _create(direct_vm, contract)
    assert int(created["stage_count"]) == 2
    assert int(created["total_amount"]) == TOTAL
    assert len(created["terms_binding"]) == 64

    workflow = contract.get_workflow(WF_ID)
    assert workflow["status"] == "open"
    assert workflow["client"].lower() == CLIENT.lower()
    assert workflow["remaining_amount"] == TOTAL
    assert workflow["terms_binding"] == created["terms_binding"]
    assert workflow["failing_index"] == -1

    stages = contract.get_workflow_stages(WF_ID)["stages"]
    assert [s["status"] for s in stages] == ["pending", "pending"]
    assert [s["amount"] for s in stages] == AMOUNTS
    assert [s["worker"] for s in stages] == [WORKER_A.lower(), WORKER_B.lower()]

    with direct_vm.expect_revert("Workflow already exists"):
        _create(direct_vm, contract)
    with direct_vm.expect_revert("Workflow not found"):
        contract.get_workflow("missing")


def _create_oversized_workers(direct_vm, contract, total):
    direct_vm.sender = CLIENT_ADDR
    direct_vm.value = total
    return contract.create_workflow(
        workflow_id="oversized-workers",
        workers_json=json.dumps([WORKER_A, WORKER_B]) + " " * 3000,
        amounts_json=json.dumps(AMOUNTS),
        stage_specs_json=json.dumps(SPECS),
        submit_window_seconds=3600,
        failure_window_seconds=600,
        settlement_grace_seconds=600,
    )


def _create_oversized_amounts(direct_vm, contract, total):
    direct_vm.sender = CLIENT_ADDR
    direct_vm.value = total
    return contract.create_workflow(
        workflow_id="oversized-amounts",
        workers_json=json.dumps([WORKER_A, WORKER_B]),
        amounts_json=json.dumps(AMOUNTS) + " " * 3000,
        stage_specs_json=json.dumps(SPECS),
        submit_window_seconds=3600,
        failure_window_seconds=600,
        settlement_grace_seconds=600,
    )


def _create_bad_json(direct_vm, contract, total):
    direct_vm.sender = CLIENT_ADDR
    direct_vm.value = total
    return contract.create_workflow(
        workflow_id="bad-json",
        workers_json="{oops",
        amounts_json=json.dumps(AMOUNTS),
        stage_specs_json=json.dumps(SPECS),
        submit_window_seconds=3600,
        failure_window_seconds=600,
        settlement_grace_seconds=600,
    )


def test_submit_stage_auth_binding_and_reuse(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract)

    direct_vm.sender = STRANGER_ADDR
    with direct_vm.expect_revert("Only the stage worker can submit its artifact"):
        contract.submit_stage(WF_ID, 0, ARTIFACT_A_URL, _sha(ARTIFACT_A))
    direct_vm.sender = WORKER_B_ADDR
    with direct_vm.expect_revert("Only the stage worker can submit its artifact"):
        contract.submit_stage(WF_ID, 0, ARTIFACT_A_URL, _sha(ARTIFACT_A))

    direct_vm.sender = WORKER_A_ADDR
    with direct_vm.expect_revert("Stage index is out of range"):
        contract.submit_stage(WF_ID, 5, ARTIFACT_A_URL, _sha(ARTIFACT_A))
    with direct_vm.expect_revert("Evidence URL must use a public hostname"):
        contract.submit_stage(WF_ID, 0, "http://127.0.0.1/artifact.json", _sha(ARTIFACT_A))
    with direct_vm.expect_revert("Artifact sha256 must be 64 hex characters"):
        contract.submit_stage(WF_ID, 0, ARTIFACT_A_URL, "abc")

    submitted = _submit(direct_vm, contract, 0)
    expected = hashlib.sha256(
        f"{WF_ID}|0|{ARTIFACT_A_URL}|{_sha(ARTIFACT_A)}".encode()
    ).hexdigest()
    assert submitted["artifact_binding"] == expected

    stage = contract.get_stage(WF_ID, 0)
    assert stage["status"] == "submitted"
    assert stage["artifact_binding"] == expected
    assert stage["artifact_sha256"] == _sha(ARTIFACT_A)

    with direct_vm.expect_revert("Stage artifact was already submitted"):
        _submit(direct_vm, contract, 0)
    with direct_vm.expect_revert(
        "Artifact already used for another stage in this workflow"
    ):
        _submit(
            direct_vm,
            contract,
            1,
            worker=WORKER_B_ADDR,
            url=ARTIFACT_A_URL,
            body=ARTIFACT_A,
        )

    direct_vm.warp((datetime.now() + timedelta(seconds=7200)).isoformat())
    with direct_vm.expect_revert("Submission deadline has passed"):
        _submit(direct_vm, contract, 1, worker=WORKER_B_ADDR)


def test_accept_stage_pays_and_closes(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract)
    _submit(direct_vm, contract, 0)
    _submit(direct_vm, contract, 1)

    direct_vm.sender = STRANGER_ADDR
    with direct_vm.expect_revert("Only the client can accept a stage"):
        contract.accept_stage(WF_ID, 0)

    direct_vm.sender = CLIENT_ADDR
    result = contract.accept_stage(WF_ID, 0)
    assert result["released_amount"] == AMOUNTS[0]
    assert contract.get_stage(WF_ID, 0)["status"] == "accepted"
    assert contract.get_workflow(WF_ID)["released_amount"] == AMOUNTS[0]

    with direct_vm.expect_revert("Stage is not awaiting a decision"):
        contract.accept_stage(WF_ID, 0)
    with direct_vm.expect_revert("Failure can only target a submitted stage"):
        contract.file_failure(WF_ID, 0, REPORT_URL, _sha(REPORT))

    contract.accept_stage(WF_ID, 1)
    workflow = contract.get_workflow(WF_ID)
    assert workflow["status"] == "closed"
    assert workflow["released_amount"] == TOTAL
    assert workflow["remaining_amount"] == 0

    with direct_vm.expect_revert("Workflow is not open"):
        contract.accept_stage(WF_ID, 0)


def test_file_failure_guards_and_binding(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract, failure_window=60)
    _submit(direct_vm, contract, 0)

    direct_vm.sender = STRANGER_ADDR
    with direct_vm.expect_revert("Only the client can file a failure"):
        contract.file_failure(WF_ID, 0, REPORT_URL, _sha(REPORT))
    direct_vm.sender = CLIENT_ADDR
    with direct_vm.expect_revert("Failure can only target a submitted stage"):
        contract.file_failure(WF_ID, 1, REPORT_URL, _sha(REPORT))
    with direct_vm.expect_revert("Evidence URL must use a public hostname"):
        contract.file_failure(WF_ID, 0, "http://localhost/report.txt", _sha(REPORT))
    with direct_vm.expect_revert("Failure report sha256 must be 64 hex characters"):
        contract.file_failure(WF_ID, 0, REPORT_URL, "nope")

    filed = _file_failure(direct_vm, contract, 0)
    expected = hashlib.sha256(
        f"{WF_ID}|failure|0|{REPORT_URL}|{_sha(REPORT)}".encode()
    ).hexdigest()
    assert filed["failure_binding"] == expected

    workflow = contract.get_workflow(WF_ID)
    assert workflow["status"] == "claimed"
    assert workflow["failing_index"] == 0
    assert workflow["failure_binding"] == expected

    with direct_vm.expect_revert("Workflow is not open"):
        _file_failure(direct_vm, contract, 0)
    with direct_vm.expect_revert("Workflow is not open"):
        _submit(direct_vm, contract, 1, worker=WORKER_B_ADDR)

    # A second workflow proves the failure window is enforced.
    _create(direct_vm, contract, workflow_id="late-claim", failure_window=1)
    _submit(direct_vm, contract, 0, workflow_id="late-claim")
    direct_vm.warp((datetime.now() + timedelta(days=2)).isoformat())
    with direct_vm.expect_revert("Failure window has passed"):
        _file_failure(direct_vm, contract, 0, workflow_id="late-claim")


def test_resolve_at_fault_refunds_named_stage(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract)
    _submit(direct_vm, contract, 0)
    _submit(direct_vm, contract, 1)
    _file_failure(direct_vm, contract, 0)

    _mock_evidence(direct_vm, artifact=ARTIFACT_A, report=REPORT)
    _mock_verdict(direct_vm, FAULT_JSON)

    result = contract.resolve_failure(WF_ID)
    assert result["verdict"] == "stage_at_fault"
    assert result["stage_at_fault"] is True
    assert result["released_amount"] == AMOUNTS[1]
    assert result["refunded_amount"] == AMOUNTS[0]

    assert contract.get_stage(WF_ID, 0)["status"] == "refunded"
    assert contract.get_stage(WF_ID, 1)["status"] == "paid"
    workflow = contract.get_workflow(WF_ID)
    assert workflow["status"] == "closed"
    assert workflow["verdict"] == "stage_at_fault"
    assert workflow["released_amount"] == AMOUNTS[1]
    assert workflow["refunded_amount"] == AMOUNTS[0]
    assert workflow["remaining_amount"] == 0
    assert len(result["verdict_binding"]) == 64
    assert result["verdict_binding"] == workflow["verdict_binding"]

    with direct_vm.expect_revert("Workflow is closed"):
        contract.resolve_failure(WF_ID)
    with direct_vm.expect_revert("Workflow is closed"):
        contract.settle_workflow(WF_ID)


def test_resolve_not_at_fault_pays_named_stage(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract)
    _submit(direct_vm, contract, 0)
    _file_failure(direct_vm, contract, 0)

    _mock_evidence(direct_vm, artifact=ARTIFACT_A, report=REPORT)
    _mock_verdict(direct_vm, NO_FAULT_JSON)

    result = contract.resolve_failure(WF_ID)
    assert result["verdict"] == "stage_not_at_fault"
    assert result["released_amount"] == AMOUNTS[0]
    assert result["refunded_amount"] == AMOUNTS[1]

    assert contract.get_stage(WF_ID, 0)["status"] == "paid"
    assert contract.get_stage(WF_ID, 1)["status"] == "refunded"
    assert contract.get_workflow(WF_ID)["remaining_amount"] == 0


def test_resolve_withheld_artifact_favours_the_claim(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract, workflow_id="unreachable")
    _submit(direct_vm, contract, 0, workflow_id="unreachable")
    _file_failure(direct_vm, contract, 0, workflow_id="unreachable")

    direct_vm.mock_web(re.escape(ARTIFACT_A_URL), {"status": 404, "body": "gone"})
    direct_vm.mock_web(re.escape(REPORT_URL), {"status": 200, "body": REPORT})
    result = contract.resolve_failure("unreachable")
    assert result["verdict"] == "stage_at_fault"
    assert contract.get_stage("unreachable", 0)["status"] == "refunded"

    _create(direct_vm, contract, workflow_id="substituted")
    _submit(direct_vm, contract, 0, workflow_id="substituted")
    _file_failure(direct_vm, contract, 0, workflow_id="substituted")
    direct_vm.clear_mocks()
    direct_vm.mock_web(re.escape(ARTIFACT_A_URL), {"status": 200, "body": "SWAPPED"})
    direct_vm.mock_web(re.escape(REPORT_URL), {"status": 200, "body": REPORT})
    result = contract.resolve_failure("substituted")
    assert result["verdict"] == "stage_at_fault"
    assert "not the one that was committed" in result["reasoning"]

    _create(direct_vm, contract, workflow_id="bad-report")
    _submit(direct_vm, contract, 0, workflow_id="bad-report")
    _file_failure(direct_vm, contract, 0, workflow_id="bad-report")
    direct_vm.clear_mocks()
    direct_vm.mock_web(re.escape(ARTIFACT_A_URL), {"status": 200, "body": ARTIFACT_A})
    direct_vm.mock_web(re.escape(REPORT_URL), {"status": 200, "body": "TAMPERED"})
    result = contract.resolve_failure("bad-report")
    assert result["verdict"] == "stage_not_at_fault"
    assert "report" in result["reasoning"].lower()


def test_resolve_unusable_llm_output_and_legacy_bool(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract)
    _submit(direct_vm, contract, 0)
    _file_failure(direct_vm, contract, 0)

    _mock_evidence(direct_vm)
    _mock_verdict(direct_vm, json.dumps({"verdict": "maybe", "reasoning": "hmm"}))
    result = contract.resolve_failure(WF_ID)
    assert result["verdict"] == "stage_not_at_fault"
    assert "no usable verdict" in result["reasoning"]

    _create(direct_vm, contract, workflow_id="legacy-bool")
    _submit(direct_vm, contract, 0, workflow_id="legacy-bool")
    _file_failure(direct_vm, contract, 0, workflow_id="legacy-bool")
    direct_vm.clear_mocks()
    _mock_evidence(direct_vm)
    _mock_verdict(direct_vm, json.dumps({"stage_at_fault": True, "reasoning": "bad"}))
    result = contract.resolve_failure("legacy-bool")
    assert result["verdict"] == "stage_at_fault"


def test_validator_requires_exact_verdict_category(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract)
    _submit(direct_vm, contract, 0)
    _file_failure(direct_vm, contract, 0)

    _mock_evidence(direct_vm)
    _mock_verdict(direct_vm, FAULT_JSON)
    contract.resolve_failure(WF_ID)

    # Validator sees the opposite verdict for the same evidence -> reject.
    direct_vm.clear_mocks()
    _mock_evidence(direct_vm)
    _mock_verdict(direct_vm, NO_FAULT_JSON)
    assert direct_vm.run_validator() is False

    # Same verdict -> accept.
    direct_vm.clear_mocks()
    _mock_evidence(direct_vm)
    _mock_verdict(direct_vm, FAULT_JSON)
    assert direct_vm.run_validator() is True

    # Malformed leader payloads are always rejected.
    assert direct_vm.run_validator(
        leader_result={"verdict": "stage_at_fault"}
    ) is False
    assert direct_vm.run_validator(
        leader_result={"verdict": "maybe", "reasoning": "x"}
    ) is False
    assert direct_vm.run_validator(
        leader_result={"verdict": "stage_at_fault", "reasoning": ""}
    ) is False


def test_settle_without_claim_after_windows(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract, submit_window=60, failure_window=60, grace=60)
    _submit(direct_vm, contract, 0)

    with direct_vm.expect_revert(
        "Failure window and settlement grace have not passed"
    ):
        contract.settle_workflow(WF_ID)

    direct_vm.warp((datetime.now() + timedelta(seconds=400)).isoformat())
    result = contract.settle_workflow(WF_ID)
    assert result["verdict"] == "settled_without_claim"
    assert result["released_amount"] == AMOUNTS[0]
    assert result["refunded_amount"] == AMOUNTS[1]

    assert contract.get_stage(WF_ID, 0)["status"] == "paid"
    assert contract.get_stage(WF_ID, 1)["status"] == "refunded"
    workflow = contract.get_workflow(WF_ID)
    assert workflow["status"] == "closed"
    assert workflow["remaining_amount"] == 0

    with direct_vm.expect_revert("Workflow is closed"):
        contract.settle_workflow(WF_ID)


def test_claim_lapse_settles_neutral(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract)
    _submit(direct_vm, contract, 0)
    _file_failure(direct_vm, contract, 0)

    with direct_vm.expect_revert("Failure claim is still within its resolution window"):
        contract.settle_workflow(WF_ID)

    direct_vm.warp((datetime.now() + timedelta(days=8)).isoformat())
    result = contract.settle_workflow(WF_ID)
    assert result["verdict"] == "claim_lapsed"
    assert result["released_amount"] == AMOUNTS[0]
    assert result["refunded_amount"] == AMOUNTS[1]

    assert contract.get_stage(WF_ID, 0)["status"] == "paid"
    assert contract.get_workflow(WF_ID)["verdict"] == "claim_lapsed"


def test_mixed_accept_and_fault_settlement_is_exact(direct_vm, direct_deploy):
    contract = direct_deploy("contracts/workflow_fault_escrow.py")
    _create(direct_vm, contract)
    _submit(direct_vm, contract, 0)
    _submit(direct_vm, contract, 1)

    direct_vm.sender = CLIENT_ADDR
    contract.accept_stage(WF_ID, 0)
    _file_failure(direct_vm, contract, 1)

    direct_vm.clear_mocks()
    _mock_evidence(direct_vm, artifact=ARTIFACT_B, report=REPORT)
    _mock_verdict(direct_vm, FAULT_JSON)
    result = contract.resolve_failure(WF_ID)
    assert result["released_amount"] == 0
    assert result["refunded_amount"] == AMOUNTS[1]

    workflow = contract.get_workflow(WF_ID)
    assert workflow["released_amount"] == AMOUNTS[0]
    assert workflow["refunded_amount"] == AMOUNTS[1]
    assert workflow["released_amount"] + workflow["refunded_amount"] == TOTAL
    assert contract.get_stage(WF_ID, 0)["status"] == "accepted"
    assert contract.get_stage(WF_ID, 1)["status"] == "refunded"
