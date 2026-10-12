"""The real-provider code path, exercised end to end against a local mock provider.

``examples/agentic_risk_review.py --llm anthropic|openai`` builds Keelgate's real
``AnthropicClient`` or ``OpenAIClient`` around the vendor's real SDK. Here the SDK talks to a
tiny local server that speaks the provider's wire format and answers with the demo planner and
drafter, and the key is a **simulated** one. So this proves the plumbing for real: the SDK
request shape and headers, the key from the environment, the planner and writer calls, the
verifier's rejection and the revision, the approval request, and the audit chain. It does
**not** prove anything about a real model's behaviour: the mock's answers are the scripted ones.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tycheon.agents.demo import draft_from_prompt, plan_reply
from tycheon.services import sample_news

pytest.importorskip("anthropic")
pytest.importorskip("openai")

pytestmark = pytest.mark.eval

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "agentic_risk_review.py"
SIMULATED_KEY = "sk-sim-0000000000000000000000000000-NOT-A-REAL-KEY"  # pragma: allowlist secret


def _example_constants() -> tuple[dict[str, float], dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("agentic_example", EXAMPLE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PORTFOLIO, module.TRADE


class MockProvider:
    """A local server speaking Anthropic's Messages API and OpenAI's chat-completions API."""

    def __init__(self, *, key: str = SIMULATED_KEY, plant_error: bool = True) -> None:
        self.key, self.plant_error = key, plant_error
        self.requests: list[dict[str, Any]] = []
        self._portfolio, self._trade = _example_constants()
        self._lock = threading.Lock()
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:  # keep the test output clean
                return

            def _send(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                headers = {k.lower(): v for k, v in self.headers.items()}
                with provider._lock:
                    provider.requests.append({"path": self.path, "headers": headers, "body": body})
                    index = len(provider.requests) - 1
                if self.path.endswith("/v1/messages"):
                    authorised = headers.get("x-api-key") == provider.key
                    system = body.get("system", "")
                    if isinstance(system, list):
                        system = " ".join(block.get("text", "") for block in system)
                    prompt = next(
                        (m["content"] for m in reversed(body["messages"]) if m["role"] == "user"),
                        "",
                    )
                    if isinstance(prompt, list):
                        prompt = " ".join(b.get("text", "") for b in prompt)
                elif self.path.endswith("/chat/completions"):
                    authorised = headers.get("authorization") == f"Bearer {provider.key}"
                    system = next(
                        (m["content"] for m in body["messages"] if m["role"] == "system"), ""
                    )
                    prompt = next(
                        (m["content"] for m in reversed(body["messages"]) if m["role"] == "user"),
                        "",
                    )
                else:
                    self._send(404, {"error": {"message": "no such route"}})
                    return
                if not authorised:
                    self._send(
                        401,
                        {
                            "type": "error",
                            "error": {
                                "type": "authentication_error",
                                "message": "invalid x-api-key",
                            },
                        },
                    )
                    return
                text = provider.answer(index, system, prompt)
                if self.path.endswith("/v1/messages"):
                    self._send(
                        200,
                        {
                            "id": f"msg_sim_{index}",
                            "type": "message",
                            "role": "assistant",
                            "model": body["model"],
                            "stop_reason": "end_turn",
                            "stop_sequence": None,
                            "content": [{"type": "text", "text": text}],
                            "usage": {
                                "input_tokens": len(prompt) // 4,
                                "output_tokens": len(text) // 4,
                            },
                        },
                    )
                else:
                    self._send(
                        200,
                        {
                            "id": f"chatcmpl-sim-{index}",
                            "object": "chat.completion",
                            "created": 0,
                            "model": body["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": text},
                                }
                            ],
                            "usage": {
                                "prompt_tokens": len(prompt) // 4,
                                "completion_tokens": len(text) // 4,
                                "total_tokens": (len(prompt) + len(text)) // 4,
                            },
                        },
                    )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def answer(self, index: int, _system: str, prompt: str) -> str:
        """The first call is the planner; later calls are drafts (the first one plants an error)."""
        if index == 0:
            return plan_reply(list(self._portfolio), trade=self._trade)
        return draft_from_prompt(prompt, plant_error=self.plant_error and index == 1)

    def __enter__(self) -> MockProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def run_example(
    provider_name: str, url: str, key: str, model: str
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "OPENAI_"))}
    if provider_name == "anthropic":
        env.update(ANTHROPIC_API_KEY=key, ANTHROPIC_BASE_URL=url)
    else:
        env.update(OPENAI_API_KEY=key, OPENAI_BASE_URL=f"{url}/v1")
    return subprocess.run(  # noqa: S603 - fixed arguments: the interpreter running this test
        [sys.executable, str(EXAMPLE), "--llm", provider_name, "--model", model],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        check=False,
    )


@pytest.fixture(scope="module", params=["anthropic", "openai"])
def run(request):
    name = request.param
    model = "claude-sonnet-5-5" if name == "anthropic" else "gpt-sim-1"
    with MockProvider() as provider:
        done = run_example(name, provider.url, SIMULATED_KEY, model)
        yield name, model, provider, done


def test_the_example_completes_through_the_real_sdk(run) -> None:
    name, model, _, done = run
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    assert f"model: {name}/{model}" in done.stdout


def test_the_verifier_rejects_the_first_draft_and_accepts_the_revision(run) -> None:
    _, _, _, done = run
    assert "draft 1: REVISE" in done.stdout and "does not match any number" in done.stdout
    assert "draft 2: ACCEPT" in done.stdout and "Status: verified" in done.stdout


def test_the_paper_trade_needs_approval_and_nothing_executes(run) -> None:
    _, _, _, done = run
    assert "propose_paper_trade APPROVAL_REQUIRED" in done.stdout
    assert "paper orders executed: 0" in done.stdout
    assert "AUDIT chain: OK" in done.stdout


def test_the_simulated_key_never_appears_in_any_output(run) -> None:
    _, _, _, done = run
    assert SIMULATED_KEY not in done.stdout and SIMULATED_KEY not in done.stderr


def test_the_sdk_sent_a_correct_request_with_the_key_from_the_environment(run) -> None:
    name, model, provider, _ = run
    assert len(provider.requests) >= 3  # a planner call and at least two drafts
    first = provider.requests[0]
    assert first["body"]["model"] == model
    # OpenAI's current API names the limit max_completion_tokens; Anthropic's is max_tokens
    limit = first["body"].get("max_tokens") or first["body"].get("max_completion_tokens")
    assert limit and limit > 0
    if name == "anthropic":
        assert first["path"].endswith("/v1/messages")
        assert first["headers"]["x-api-key"] == SIMULATED_KEY
        assert first["headers"].get("anthropic-version")
        assert first["body"].get("system") and first["body"]["messages"][0]["role"] == "user"
    else:
        assert first["path"].endswith("/v1/chat/completions")
        assert first["headers"]["authorization"] == f"Bearer {SIMULATED_KEY}"
        assert [m["role"] for m in first["body"]["messages"][:2]] == ["system", "user"]


def test_untrusted_news_text_never_reaches_a_prompt(run) -> None:
    _, _, provider, _ = run
    sent = json.dumps([r["body"] for r in provider.requests])
    store = sample_news()
    documents = [
        doc for symbol in ("SYN-GBM", "SYN-GARCH", "SYN-REGIME") for doc in store.documents(symbol)
    ]
    assert documents, "the sample news store should not be empty"
    for doc in documents:
        assert doc.text not in sent and doc.title not in sent


def test_the_planners_prompt_asks_for_json_and_the_drafter_gets_evidence_ids(run) -> None:
    _, _, provider, _ = run
    texts = [json.dumps(r["body"]) for r in provider.requests]
    assert "[E1]" in texts[1] and "[E1]" in texts[-1]  # the writer is shown numbered evidence


@pytest.mark.parametrize("name", ["anthropic", "openai"])
def test_a_wrong_key_fails_cleanly_and_leaks_nothing(name) -> None:
    wrong = "sk-sim-WRONG-KEY-0000000000000000000000"  # pragma: allowlist secret
    with MockProvider() as provider:
        done = run_example(name, provider.url, wrong, "model-x")
    assert done.returncode != 0
    assert wrong not in done.stdout and wrong not in done.stderr
    assert "paper orders executed" not in done.stdout  # nothing downstream ran


def test_a_provider_that_is_down_fails_cleanly() -> None:
    done = run_example("anthropic", "http://127.0.0.1:9", SIMULATED_KEY, "model-x")
    assert done.returncode != 0 and SIMULATED_KEY not in done.stdout + done.stderr
