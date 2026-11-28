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

# DuckDB and Keelgate's native ``regopy`` cannot share a process on Linux: importing DuckDB after
# ``regopy`` is loaded corrupts the heap ("double free or corruption"), and so did using both.
# Tycheon therefore evaluates policy in a worker process (``_rego_process``) and never creates a
# Rego interpreter here. Importing ``keelgate.policy`` loads the ``regopy`` module, so DuckDB (a
# core dependency) is imported first.
#
# The exception is a process that has already loaded ``regopy``, such as ``keelgate eval run``,
# which loads it before it loads ``_outcome_metrics``. Importing DuckDB (or torch, or matplotlib on
# Python 3.14) there aborts, so this package imports nothing native in that case, and loads its
# other names only when they are asked for.
import sys
from importlib import import_module
from typing import TYPE_CHECKING

if "regopy" not in sys.modules:
    import duckdb  # noqa: F401

if TYPE_CHECKING:
    from tycheon.governance._llm import (
        KeelgateTextModel,
        make_text_model,
        scripted_text_model,
    )
    from tycheon.governance._loop import (
        KeelgateComposeRunner,
    )
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
    from tycheon.governance._serving import (
        ApproverToken,
        McpHandle,
        build_approvals_app,
        build_mcp,
    )
    from tycheon.governance._telemetry import (
        OtelTracer,
        instrument,
    )

#: Which submodule defines each public name.
_EXPORTS = {
    "KeelgateTextModel": "_llm",
    "make_text_model": "_llm",
    "scripted_text_model": "_llm",
    "KeelgateComposeRunner": "_loop",
    "ALWAYS_APPROVE": "_policy",
    "ANALYTICS_CAPABILITIES": "_policy",
    "DEFAULT_LIMITS": "_policy",
    "TycheonPolicy": "_policy",
    "AGENT_API": "_runtime",
    "AGENT_CAPABILITIES": "_runtime",
    "AGENT_MCP": "_runtime",
    "ApprovalSummary": "_runtime",
    "AuditSummary": "_runtime",
    "GovernanceError": "_runtime",
    "GovernedRuntime": "_runtime",
    "RuntimeConfig": "_runtime",
    "ApproverToken": "_serving",
    "McpHandle": "_serving",
    "build_approvals_app": "_serving",
    "build_mcp": "_serving",
    "OtelTracer": "_telemetry",
    "instrument": "_telemetry",
}


def __getattr__(name: str) -> object:
    try:
        module = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    # A missing Keelgate raises here: it fails closed, with no fallback.
    value: object = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


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
