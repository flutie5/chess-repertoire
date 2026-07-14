"""WSGI entry point for Gunicorn (Render production)."""
from webapp.app import app  # noqa: F401

__all__ = ["app"]
