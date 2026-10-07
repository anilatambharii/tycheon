"""Serving: a REST API and an MCP server over the same governed tools.

Needs the ``serve`` extra (``pip install 'tycheon[serve]'``). Both surfaces call the governed
runtime in :mod:`tycheon.governance`; neither reaches the analytics any other way.
"""

from tycheon.serve.app import create_app
from tycheon.serve.auth import ApiKeyError, ApiKeys
from tycheon.serve.jobs import JobManager

__all__ = ["ApiKeyError", "ApiKeys", "JobManager", "create_app"]
