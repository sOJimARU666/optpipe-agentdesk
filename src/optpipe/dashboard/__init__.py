"""Read-only local dashboard over out/ (Milestone 2). Owner: Moodle (UX) / Poodle (code).

FastAPI + Jinja + a little HTMX. GET routes only, bound to 127.0.0.1 by `optpipe serve`.
It reads session-<date>.v1_1.json (or upgrades the v1 file), the .trace.json and its .sha256.
Nothing here writes records, and nothing here can place an order.
"""
from .app import create_app  # noqa: F401
