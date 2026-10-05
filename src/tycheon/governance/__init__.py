"""The only module in Tycheon that may import Keelgate. Empty until Phase T4.

Keelgate is the safety harness: capability-scoped tools, policy-as-code gates,
approvals, audit and evals. Tycheon depends on it as a library from T4 onward,
and every one of those imports is confined to this package so that a Keelgate
contract change touches exactly one place.

See ``README.md`` beside this file, and the rules in ``AGENTS.md``.
"""
