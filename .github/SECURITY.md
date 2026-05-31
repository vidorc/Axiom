# Security Policy

Axiom holds users' provider credentials (BYOK). A vulnerability here is treated
as the highest-priority class of issue. Thank you for disclosing responsibly.

## Reporting a vulnerability

**Do not open a public issue or PR for a security vulnerability.**

Instead, use GitHub's private vulnerability reporting (the **"Report a
vulnerability"** button under the repository's **Security** tab), or email the
maintainers at **security@axiom.dev** (placeholder — update before public launch).

Please include:

- A description of the issue and its impact.
- Steps to reproduce (a minimal proof-of-concept if possible).
- Affected component (engine, vault, node runtime, MCP, API, CLI).
- Any suggested remediation.

We will acknowledge receipt, investigate, and coordinate a fix and disclosure
timeline with you. Please give us reasonable time to remediate before any public
disclosure.

## Scope

The threat model, trust boundaries, and the controls we commit to are documented
in [`SECURITY.md`](../SECURITY.md) at the repo root. Reports that demonstrate a
break in any of those guarantees — credential exfiltration, cross-tenant access,
SSRF/metadata theft, plaintext-secret leakage, or trust-tier bypass — are
especially valuable.

## Supported versions

Axiom is pre-release (foundation stage). Once versioned releases exist, this
section will list which are supported with security updates.
