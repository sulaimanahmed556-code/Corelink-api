"""
CORELINK API Router
"""

from fastapi import APIRouter

from app.api.routes import webhook, payments, admin
from app.api.routes import plans, subscriptions, user_management, group_dashboard

api_router = APIRouter()

api_router.include_router(webhook.router, prefix="/webhook", tags=["Webhooks"])
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
api_router.include_router(plans.router, prefix="/plans", tags=["Plans"])
api_router.include_router(subscriptions.router, prefix="/subscriptions", tags=["Subscriptions"])
api_router.include_router(user_management.router, prefix="/accounts", tags=["User Management"])
api_router.include_router(group_dashboard.router, prefix="/group", tags=["Group Dashboard"])


@api_router.get("/ping")
async def ping() -> dict:
    return {"message": "pong"}
