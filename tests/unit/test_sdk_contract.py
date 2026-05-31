"""Unit tests for the SDK node contract (SDK_SPEC.md §4).

These are pure and fast — no platform, no I/O — which is the whole point of the
SDK being import-independent. A concrete node is exercised against a *fake*
ExecutionContext built right here, proving an author can test a node without
standing up the platform.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from axiom.sdk import (
    RETRYABLE_CLASSES,
    BaseNode,
    CompensationResult,
    ErrorClass,
    ExecutionContext,
    JsonObject,
    NodeError,
    ValidationError,
    ValidationResult,
)

pytestmark = pytest.mark.unit


# ── Fakes ──────────────────────────────────────────────────────────────────────
# A minimal, dependency-free stand-in for the platform-provided context. The
# fact that this satisfies the ExecutionContext protocol structurally is itself
# part of what we're asserting (test_fake_context_satisfies_protocol).


class _FakeRun:
    def __init__(self, *, attempt: int = 1) -> None:
        self._run_id = uuid4()
        self._org_id = uuid4()
        self._attempt = attempt

    @property
    def run_id(self) -> UUID:
        return self._run_id

    @property
    def node_id(self) -> str:
        return "n1"

    @property
    def attempt(self) -> int:
        return self._attempt

    @property
    def org_id(self) -> UUID:
        return self._org_id


class _FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []

    def debug(self, event: str, **f: object) -> None:
        self.events.append(("debug", event, f))

    def info(self, event: str, **f: object) -> None:
        self.events.append(("info", event, f))

    def warning(self, event: str, **f: object) -> None:
        self.events.append(("warning", event, f))

    def error(self, event: str, **f: object) -> None:
        self.events.append(("error", event, f))


class _FakeCost:
    def __init__(self) -> None:
        self.ledger: list[tuple[int, str]] = []

    def record(self, amount: int, unit: str) -> None:
        self.ledger.append((amount, unit))


class _FakeCreds:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def __call__(self, provider: str) -> str:
        try:
            return self._secrets[provider]
        except KeyError as exc:
            raise LookupError(f"credential not declared: {provider}") from exc


class _FakeCancel:
    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self._cancelled


class _FakeContext:
    """Structurally satisfies ExecutionContext for node tests."""

    def __init__(self) -> None:
        self._run = _FakeRun()
        self._log = _FakeLog()
        self._cost = _FakeCost()
        self._creds = _FakeCreds({"apollo": "secret-key"})
        self._cancel = _FakeCancel()

    @property
    def run(self) -> _FakeRun:
        return self._run

    @property
    def log(self) -> _FakeLog:
        return self._log

    @property
    def cost(self) -> _FakeCost:
        return self._cost

    @property
    def credentials(self) -> _FakeCreds:
        return self._creds

    @property
    def cancel(self) -> _FakeCancel:
        return self._cancel

    @property
    def cache(self) -> object:  # not exercised here
        raise NotImplementedError

    @property
    def http(self) -> object:  # not exercised here
        raise NotImplementedError


# ── Reference node under test ────────────────────────────────────────────────


class _GreetNode(BaseNode):
    """Tiny node: requires a non-empty `name`, returns a greeting, costs 1 credit."""

    def validate(self, inputs: JsonObject) -> ValidationResult:
        if not inputs.get("name"):
            return ValidationResult.failure("name is required")
        return ValidationResult.success()

    async def execute(self, ctx: ExecutionContext, inputs: JsonObject) -> JsonObject:
        ctx.cost.record(1, "credit")
        ctx.log.info("greeted", name=inputs["name"])
        return {"greeting": f"hello {inputs['name']}"}


# ── Error taxonomy ─────────────────────────────────────────────────────────────


def test_retryable_classes_match_taxonomy() -> None:
    # The retryable/terminal split is the contract that makes engine retry policy
    # work (SDK_SPEC.md §4.3). Lock it down so a careless edit is caught.
    assert RETRYABLE_CLASSES == {
        ErrorClass.RATE_LIMITED,
        ErrorClass.PROVIDER_ERROR,
        ErrorClass.TIMEOUT,
    }


@pytest.mark.parametrize(
    ("error_class", "expected_retryable"),
    [
        (ErrorClass.RATE_LIMITED, True),
        (ErrorClass.PROVIDER_ERROR, True),
        (ErrorClass.TIMEOUT, True),
        (ErrorClass.INVALID_INPUT, False),
        (ErrorClass.AUTH_ERROR, False),
        (ErrorClass.NOT_FOUND, False),
        (ErrorClass.INTERNAL, False),
    ],
)
def test_node_error_retryable_property(error_class: ErrorClass, expected_retryable: bool) -> None:
    assert NodeError(error_class, "boom").retryable is expected_retryable


def test_rate_limited_carries_retry_after() -> None:
    err = NodeError(ErrorClass.RATE_LIMITED, "slow down", retry_after_seconds=2.5)
    assert err.retryable is True
    assert err.retry_after_seconds == 2.5


def test_validation_error_is_terminal_invalid_input() -> None:
    err = ValidationError("bad input")
    assert err.error_class is ErrorClass.INVALID_INPUT
    assert err.retryable is False


# ── Result value objects ────────────────────────────────────────────────────────


def test_validation_result_success_and_failure() -> None:
    assert ValidationResult.success().ok is True
    failed = ValidationResult.failure("a", "b")
    assert failed.ok is False
    assert failed.errors == ("a", "b")


def test_validation_failure_without_message_still_has_one() -> None:
    # A failure with no message would be a useless UI surface — guard against it.
    assert ValidationResult.failure().errors == ("validation failed",)


def test_compensation_result_factories() -> None:
    assert CompensationResult.compensated().status == "compensated"
    assert CompensationResult.unsupported().status == "unsupported"
    assert CompensationResult.failed("nope").status == "failed"


# ── BaseNode defaults ────────────────────────────────────────────────────────────


def test_base_node_default_validate_accepts() -> None:
    # A node that doesn't override validate() accepts any structurally-valid input.
    class _Bare(BaseNode):
        async def execute(self, ctx: ExecutionContext, inputs: JsonObject) -> JsonObject:
            return {}

    assert _Bare().validate({"anything": 1}).ok is True


async def test_base_node_default_compensate_is_unsupported() -> None:
    class _Bare(BaseNode):
        async def execute(self, ctx: ExecutionContext, inputs: JsonObject) -> JsonObject:
            return {}

    result = await _Bare().compensate(_FakeContext(), {})  # type: ignore[arg-type]
    assert result.status == "unsupported"


def test_base_node_cannot_be_instantiated_without_execute() -> None:
    with pytest.raises(TypeError):
        BaseNode()  # type: ignore[abstract]


# ── A node end to end against a fake context ─────────────────────────────────────


def test_node_validate_rejects_missing_field() -> None:
    assert _GreetNode().validate({}).ok is False


async def test_node_execute_against_fake_context() -> None:
    ctx = _FakeContext()
    out = await _GreetNode().execute(ctx, {"name": "Ada"})  # type: ignore[arg-type]
    assert out == {"greeting": "hello Ada"}
    # The node reported cost and logged through the context — not via globals.
    assert ctx.cost.ledger == [(1, "credit")]
    assert ctx.log.events == [("info", "greeted", {"name": "Ada"})]


def test_fake_context_satisfies_protocol() -> None:
    # runtime_checkable lets us assert the structural contract holds. If the
    # Protocol gains a member the fake lacks, this fails — a cheap drift alarm.
    assert isinstance(_FakeContext(), ExecutionContext)


def test_scoped_credentials_reject_undeclared_provider() -> None:
    ctx = _FakeContext()
    assert ctx.credentials("apollo") == "secret-key"
    with pytest.raises(LookupError):
        ctx.credentials("openai")  # never declared → must not silently return ""
