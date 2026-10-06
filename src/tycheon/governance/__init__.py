"""The one place Tycheon touches Keelgate.

Everything that imports ``keelgate`` lives in this package (``tests/test_architecture.py``
enforces it), so a change to Keelgate's integration contract touches exactly one place. The rest
of Tycheon asks this package for governed tool calls and gets plain results back.

Rules this package lives under (AGENTS.md):

* **It fails closed, and there is no bypass.** If Keelgate is missing, importing this package
  fails; nothing here falls back to running a tool without the harness.
* Only the documented integration contract is used, never Keelgate's internal modules.
* Tool registration, capability grants, policy loading, loop setup, approvals, audit and
  telemetry are all configured here.
"""

from tycheon.governance._llm import KeelgateTextModel, make_text_model, scripted_text_model
from tycheon.governance._loop import KeelgateComposeRunner
from tycheon.governance._policy import (
    ALWAYS_APPROVE,
    ANALYTICS_CAPABILITIES,
    DEFAULT_LIMITS,
    TycheonPolicy,
)
from tycheon.governance._runtime import (
    AGENT_API,
    AGENT_CAPABILITIES,
    AGENT_MCP,
    ApprovalSummary,
    AuditSummary,
    GovernanceError,
    GovernedRuntime,
    RuntimeConfig,
)
from tycheon.governance._serving import ApproverToken, McpHandle, build_approvals_app, build_mcp
from tycheon.governance._telemetry import OtelTracer, instrument

__all__ = [
    "AGENT_API",
    "AGENT_CAPABILITIES",
    "AGENT_MCP",
    "ALWAYS_APPROVE",
    "ANALYTICS_CAPABILITIES",
    "DEFAULT_LIMITS",
    "ApprovalSummary",
    "ApproverToken",
    "AuditSummary",
    "GovernanceError",
    "GovernedRuntime",
    "KeelgateComposeRunner",
    "KeelgateTextModel",
    "McpHandle",
    "OtelTracer",
    "RuntimeConfig",
    "TycheonPolicy",
    "build_approvals_app",
    "build_mcp",
    "instrument",
    "make_text_model",
    "scripted_text_model",
]
