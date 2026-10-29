"""Data providers behind interfaces. Owner: Doodle (sources) / Poodle (code).

Stages depend on the Protocols in base.py, never on a concrete source, so EODHD or a
read-only broker market-data API can be added later without touching the stages.
"""
