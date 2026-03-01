"""
CORELINK API Routes

Exports all route modules.
"""

from app.api.routes import webhook, payments, admin

__all__ = [
    "webhook",
    "payments",
    "admin",
]
