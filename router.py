"""
CORELINK API Router

Main router that includes all API route modules.
"""

from fastapi import APIRouter

from app.api.routes import webhook, payments, admin

# Create main API router
api_router = APIRouter()


# Include route modules
api_router.include_router(webhook.router, prefix="/webhook", tags=["Webhooks"])
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])


@api_router.get("/ping")
async def ping() -> dict:
    """
    Simple ping endpoint for testing API connectivity.
    
    Returns:
        JSON response with pong message
    """
    return {"message": "pong"}
