"""Governed agentic research workflow: planner, specialists, verifier. Empty until T4.

Every tool these agents call is registered through :mod:`tycheon.governance`,
which gates it with Keelgate. The agents never execute a side effect directly,
and external text they read (news, filings) is untrusted data, never
instructions.
"""
