"""End-to-end: a manifest node runs through the durable engine + vault (WS-1/2/3).

The proof that the three Phase-1 critical-path pieces compose: bind a credential
in the vault → author a workflow whose graph node is an ``http_manifest`` node
bound to that credential → enqueue a run → drain a worker. The manifest node
resolves the credential from the vault, injects it into the request, calls the
(mocked) provider, maps outputs, and records cost — and the secret appears in no
log event along the way.

This is the machinery behind the §3 exit gate ("enrich … with retries … accurate
per-node cost"). The one clause it does not cover is a *live provider key* — that
requires real credentials and is the operator's to run; here the provider is a
deterministic ``MockTransport`` so the chain is fully exercised without a network.
"""

from __future__ import annotations

import httpx
import pytest
import structlog
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from axiom.api.app import create_app
from axiom.composition import build_durable_engine
from axiom.platform.crypto import EnvelopeCipher, FileKeyProvider
from axiom.worker.loop import OrchestratorLoop

pytestmark = pytest.mark.integration

SECRET = "sk-apollo-E2E-DO-NOT-LEAK-abcdef123456"

# A workflow with a single Apollo-enrich manifest node, bound to a credential by
# the placeholder "{{cred}}" which the test replaces with the real id post-bind.
_PROVIDER_RESPONSE = {
    "person": {
        "name": "Grace Hopper",
        "title": "Rear Admiral",
        "organization": {"name": "US Navy"},
        "linkedin_url": "https://linkedin.com/in/ghopper",
    }
}


def _shared_cipher() -> EnvelopeCipher:
    return EnvelopeCipher(FileKeyProvider(b"e2e-manifest-key-material-000000001"))


def _mock_provider_factory(captured: dict[str, httpx.Request]):
    """An http_client_factory that records the request and returns a canned 200.

    Stands in for Apollo: whatever the manifest node sends, we capture it (to
    assert the injected auth header) and return a fixed person payload.
    """

    def _factory() -> httpx.AsyncClient:
        def _handler(request: httpx.Request) -> httpx.Response:
            captured["req"] = request
            return httpx.Response(200, json=_PROVIDER_RESPONSE)

        return httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    return _factory


def _client(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    cipher: EnvelopeCipher,
    http_factory,
) -> httpx.AsyncClient:
    """App client whose durable engine shares the test's cipher + mock provider.

    Passing ``cipher`` means the API's vault (credential bind) and the engine's
    resolver (credential resolve at run time) use the same key, so a secret bound
    over HTTP decrypts when the node runs.
    """
    app = create_app()
    app.state.durable = build_durable_engine(
        session_factory, cipher=cipher, http_client_factory=http_factory
    )
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _drain(client: httpx.AsyncClient) -> int:
    durable = client._transport.app.state.durable  # type: ignore[attr-defined]
    loop = OrchestratorLoop(engine=durable.engine, resolver=durable.resolver)
    return await loop.drain()


async def test_manifest_node_runs_with_vault_credential_end_to_end(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cipher = _shared_cipher()
    captured: dict[str, httpx.Request] = {}
    http_factory = _mock_provider_factory(captured)

    with structlog.testing.capture_logs() as logs:
        async with _client(session_factory, cipher=cipher, http_factory=http_factory) as client:
            # 1) Bind the Apollo credential through the real vault API.
            cred = (
                await client.post(
                    "/v1/credentials",
                    json={"provider": "apollo", "label": "e2e", "secret": SECRET},
                )
            ).json()

            # 2) Author a workflow: one apollo-enrich node bound to that credential.
            graph = {
                "nodes": [
                    {
                        "id": "enrich",
                        "node_ref": "axiom/apollo-enrich",
                        "credentials": {"apollo": cred["id"]},
                    }
                ],
                "edges": [],
            }
            wf = (
                await client.post(
                    "/v1/workflows",
                    json={"name": "e2e-enrich", "graph": graph},
                )
            ).json()

            # 3) Start a run with an input email, then drain a worker to completion.
            run = (
                await client.post(
                    f"/v1/workflows/{wf['workflow_id']}/runs",
                    json={"input": {"email": "grace@example.com"}},
                )
            ).json()
            drained = await _drain(client)
            assert drained >= 1

            # 4) Read the run back: it succeeded, the node output was mapped, cost recorded.
            detail = (await client.get(f"/v1/runs/{run['id']}")).json()

    assert detail["status"] == "succeeded", detail
    node = next(n for n in detail["nodes"] if n["node_id"] == "enrich")
    assert node["status"] == "succeeded"
    assert node["output"] == {
        "full_name": "Grace Hopper",
        "title": "Rear Admiral",
        "company": "US Navy",
        "linkedin": "https://linkedin.com/in/ghopper",
    }
    # The node recorded its cost (1 credit per the manifest cost_model). The
    # persisted `cost_cents` rollup is 0 here because the spend is denominated in
    # *credits*, and credit→cent conversion is a billing concern (Phase 12) — the
    # CostLedger keeps the raw entry but only sums cent-denominated units into
    # cost_cents. So 0 is the correct, documented value for a credit-priced node.
    assert node["cost_cents"] == 0

    # The credential was actually injected into the provider request as the header.
    assert captured["req"].headers["X-Api-Key"] == SECRET

    # And it leaked into no log event along the entire run.
    assert SECRET not in repr(logs), "credential leaked into a log event during the run"


async def test_manifest_node_maps_provider_error_to_retry_taxonomy(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A 503 from the provider becomes a retryable provider_error, surfaced on the run."""
    cipher = _shared_cipher()

    def _factory() -> httpx.AsyncClient:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "upstream down"})

        return httpx.AsyncClient(transport=httpx.MockTransport(_handler))

    async with _client(session_factory, cipher=cipher, http_factory=_factory) as client:
        cred = (
            await client.post(
                "/v1/credentials",
                json={"provider": "apollo", "label": "e2e", "secret": SECRET},
            )
        ).json()
        graph = {
            "nodes": [
                {
                    "id": "enrich",
                    "node_ref": "axiom/apollo-enrich",
                    "credentials": {"apollo": cred["id"]},
                    # Single attempt so the run settles fast on the terminal-after-retries path.
                    "retry": {"max_attempts": 1},
                }
            ],
            "edges": [],
        }
        wf = (await client.post("/v1/workflows", json={"name": "e2e-err", "graph": graph})).json()
        run = (
            await client.post(
                f"/v1/workflows/{wf['workflow_id']}/runs",
                json={"input": {"email": "grace@example.com"}},
            )
        ).json()
        await _drain(client)
        detail = (await client.get(f"/v1/runs/{run['id']}")).json()

    node = next(n for n in detail["nodes"] if n["node_id"] == "enrich")
    assert node["status"] == "failed"
    assert node["error"]["error_class"] == "provider_error"
    assert node["error"]["retryable"] is True
