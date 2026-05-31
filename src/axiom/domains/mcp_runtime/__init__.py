"""MCP Runtime domain (BLUEPRINT.md §4, DOMAIN_MODEL.md §6, SECURITY.md §9).

Owns: MCP server registration, discovery, the MCP client, permissions, health.

A specialization of node_runtime — an MCP server is "a node whose implementation
is a remote tool call" (ADR-0006). Built as a node kind, not a parallel universe.
All MCP output is treated as untrusted data, never instructions (SECURITY.md §9).

Deferred to Phase 4. The package exists now only to reserve the boundary.
"""
