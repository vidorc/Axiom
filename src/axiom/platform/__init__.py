"""Platform layer — infrastructure shared by all domains.

Config, logging, database, and cache/transport wiring. Depends only on
axiom.shared. Domains depend on this; this never depends on domains.
"""
