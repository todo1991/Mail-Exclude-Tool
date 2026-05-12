"""Gunicorn entrypoint. Runs bootstrap on import so the WSGI worker
has DB schema + auto-sync thread ready before serving requests."""
from app import app, init_schema, start_auto_sync

init_schema()
start_auto_sync()
