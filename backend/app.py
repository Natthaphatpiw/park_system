"""Vercel recognizes this FastAPI entrypoint without legacy builds/rewrites."""
from parking.api import create_app

app = create_app()
