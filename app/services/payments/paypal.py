"""
CORELINK PayPal Payment Integration

Handles subscription management and webhook processing for PayPal payments.
"""

import base64
import httpx
from typing import Any, Optional
from datetime import datetime
from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Subscription, Group
from app.models.subscription import PaymentProvider, SubscriptionStatus


# PayPal API base URLs
def get_paypal_base_url() -> str:
    """Get PayPal API base URL based on environment."""
    if settings.PAYPAL_MODE == "live":
        return "https://api-m.paypal.com"
    return "https://api-m.sandbox.paypal.com"


async def get_access_token() -> str:
    """
    Get PayPal OAuth access token.
    
    Uses client credentials flow to authenticate with PayPal API.
    
    Returns:
        Access token string
        
    Raises:
        httpx.HTTPError: If authentication fails
    """
    try:
        logger.debug("Requesting PayPal access token")
        
        # Encode credentials
        credentials = f"{settings.PAYPAL_CLIENT_ID}:{settings.PAYPAL_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{get_paypal_base_url()}/v1/oauth2/token",
                headers={
                    "Authorization": f"Basic {encoded_credentials}",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                data={"grant_type": "client_credentials"}
            )
            
            response.raise_for_status()
            data = response.json()
            
            access_token = data.get("access_token")
            logger.success("PayPal access token obtained")
            
            return access_token
            
    except httpx.HTTPError as e:
        logger.error(f"PayPal authentication error: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error getting access token: {str(e)}")
        raise


async def create_subscription(
    plan_id: str,
    subscriber_email: str,
    group_id: Optional[str] = None
) -> dict[str, Any]:
    """
    Create a new PayPal subscription.
    
    Initializes a subscription for a customer with the specified plan.
    
    Args:
        plan_id: PayPal plan ID (e.g., "P-...")
        subscriber_email: Subscriber email address
        group_id: Optional group ID to store in custom_id
        
    Returns:
        Dictionary with subscription data:
        {
            "subscription_id": "I-...",
            "status": "APPROVAL_PENDING",
            "approval_url": "https://...",
            "create_time": "2026-01-15T10:00:00Z"
        }
        
    Raises:
        httpx.HTTPError: If PayPal API call fails
        
    Usage:
        from app.services.payments.paypal import create_subscription
        
        subscription = await create_subscription(
            plan_id="P-abc123",
            subscriber_email="user@example.com",
            group_id="group-uuid"
        )
    """
    try:
        logger.info(f"Creating PayPal subscription for: {subscriber_email}")
        
        access_token = await get_access_token()
        
        # Build request payload
        payload = {
            "plan_id": plan_id,
            "subscriber": {
                "email_address": subscriber_email
            },
            "application_context": {
                "brand_name": "CORELINK",
                "locale": "en-US",
                "shipping_preference": "NO_SHIPPING",
                "user_action": "SUBSCRIBE_NOW",
                "payment_method": {
                    "payer_selected": "PAYPAL",
                    "payee_preferred": "IMMEDIATE_PAYMENT_REQUIRED"
                },
                "return_url": f"{settings.API_V1_PREFIX}/payments/paypal/success",
                "cancel_url": f"{settings.API_V1_PREFIX}/payments/paypal/cancel"
            }
        }
        
        # Add custom_id for tracking
        if group_id:
            payload["custom_id"] = group_id
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{get_paypal_base_url()}/v1/billing/subscriptions",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Prefer": "return=representation"
                },
                json=payload
            )
            
            response.raise_for_status()
            data = response.json()
            
            # Extract approval URL
            approval_url = None
            for link in data.get("links", []):
                if link.get("rel") == "approve":
                    approval_url = link.get("href")
                    break
            
            result = {
                "subscription_id": data.get("id"),
                "status": data.get("status"),
                "approval_url": approval_url,
                "create_time": data.get("create_time")
            }
            
            logger.success(
                f"Subscription created: {result['subscription_id']}, "
                f"status={result['status']}"
            )
            
            return result
            
    except httpx.HTTPError as e:
        logger.error(f"PayPal HTTP error: {str(e)}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response body: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error creating subscription: {str(e)}")
        raise


async def cancel_subscription(
    subscription_id: str,
    reason: str = "Customer request"
) -> dict[str, Any]:
    """
    Cancel a PayPal subscription.
    
    Cancels an active subscription immediately.
    
    Args:
        subscription_id: PayPal subscription ID (e.g., "I-...")
        reason: Cancellation reason
        
    Returns:
        Dictionary with cancellation result:
        {
            "subscription_id": "I-...",
            "status": "CANCELLED",
            "cancelled_at": "2026-01-15T10:00:00Z"
        }
        
    Raises:
        httpx.HTTPError: If PayPal API call fails
        
    Usage:
        from app.services.payments.paypal import cancel_subscription
        
        result = await cancel_subscription(
            subscription_id="I-abc123",
            reason="User requested cancellation"
        )
    """
    try:
        logger.info(f"Canceling PayPal subscription: {subscription_id}")
        
        access_token = await get_access_token()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{get_paypal_base_url()}/v1/billing/subscriptions/{subscription_id}/cancel",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                },
                json={"reason": reason}
            )
            
            response.raise_for_status()
            
            # Get updated subscription details
            details = await get_subscription_details(subscription_id)
            
            result = {
                "subscription_id": subscription_id,
                "status": details.get("status"),
                "cancelled_at": datetime.utcnow().isoformat()
            }
            
            logger.success(f"Subscription canceled: {subscription_id}")
            
            return result
            
    except httpx.HTTPError as e:
        logger.error(f"PayPal HTTP error: {str(e)}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response body: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error canceling subscription: {str(e)}")
        raise


async def get_subscription_details(subscription_id: str) -> dict[str, Any]:
    """
    Get details of a PayPal subscription.
    
    Args:
        subscription_id: PayPal subscription ID
        
    Returns:
        Dictionary with subscription details
        
    Raises:
        httpx.HTTPError: If PayPal API call fails
    """
    try:
        logger.info(f"Fetching PayPal subscription: {subscription_id}")
        
        access_token = await get_access_token()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{get_paypal_base_url()}/v1/billing/subscriptions/{subscription_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            logger.success(f"Subscription details fetched: {subscription_id}")
            
            return data
            
    except httpx.HTTPError as e:
        logger.error(f"PayPal HTTP error: {str(e)}")
        raise


async def verify_webhook(payload: dict, headers: dict) -> dict[str, Any]:
    """
    Verify PayPal webhook signature.
    
    Validates that the webhook event came from PayPal by verifying
    the webhook signature using PayPal's verification API.
    
    Args:
        payload: Webhook event payload (as dict)
        headers: Request headers containing webhook signature
        
    Returns:
        Dictionary with verification result:
        {
            "verification_status": "SUCCESS",
            "event_type": "BILLING.SUBSCRIPTION.ACTIVATED"
        }
        
    Raises:
        Exception: If verification fails
        
    Usage:
        from app.services.payments.paypal import verify_webhook
        
        @router.post("/webhook/paypal")
        async def paypal_webhook(request: Request):
            payload = await request.json()
            headers = dict(request.headers)
            
            try:
                result = await verify_webhook(payload, headers)
                if result["verification_status"] == "SUCCESS":
                    # Process event
                    pass
            except Exception:
                raise HTTPException(status_code=401)
    """
    try:
        logger.debug("Verifying PayPal webhook signature")
        
        access_token = await get_access_token()
        
        # Get webhook ID from config (must be set up in PayPal dashboard)
        webhook_id = settings.PAYPAL_WEBHOOK_ID if hasattr(settings, 'PAYPAL_WEBHOOK_ID') else None
        
        if not webhook_id:
            logger.warning("PAYPAL_WEBHOOK_ID not configured, skipping verification")
            return {
                "verification_status": "SKIPPED",
                "event_type": payload.get("event_type")
            }
        
        # Build verification request
        verification_payload = {
            "auth_algo": headers.get("paypal-auth-algo"),
            "cert_url": headers.get("paypal-cert-url"),
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "webhook_id": webhook_id,
            "webhook_event": payload
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{get_paypal_base_url()}/v1/notifications/verify-webhook-signature",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                },
                json=verification_payload
            )
            
            response.raise_for_status()
            data = response.json()
            
            verification_status = data.get("verification_status")
            
            if verification_status == "SUCCESS":
                logger.success("Webhook signature verified")
                return {
                    "verification_status": verification_status,
                    "event_type": payload.get("event_type")
                }
            else:
                logger.warning(f"Webhook verification failed: {verification_status}")
                raise Exception(f"Webhook verification failed: {verification_status}")
            
    except httpx.HTTPError as e:
        logger.error(f"PayPal webhook verification error: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error verifying webhook: {str(e)}")
        raise


async def handle_subscription_activated(event_data: dict) -> None:
    """
    Handle BILLING.SUBSCRIPTION.ACTIVATED webhook event.
    
    Updates subscription record in database when subscription becomes active.
    
    Args:
        event_data: PayPal event resource object
    """
    try:
        subscription_id = event_data.get("id")
        custom_id = event_data.get("custom_id")  # Contains group_id
        
        logger.info(f"Processing BILLING.SUBSCRIPTION.ACTIVATED: {subscription_id}")
        
        if not custom_id:
            logger.warning(f"No custom_id (group_id) in subscription: {subscription_id}")
            return
        
        group_id = custom_id
        
        # Get billing info
        billing_info = event_data.get("billing_info", {})
        next_billing_time = billing_info.get("next_billing_time")
        
        current_period_end = None
        if next_billing_time:
            current_period_end = datetime.fromisoformat(
                next_billing_time.replace('Z', '+00:00')
            )
        
        async with AsyncSessionLocal() as db:
            # Get or create subscription record
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if subscription:
                # Update existing
                subscription.provider = PaymentProvider.PAYPAL
                subscription.status = SubscriptionStatus.ACTIVE
                subscription.current_period_end = current_period_end
            else:
                # Create new
                subscription = Subscription(
                    group_id=group_id,
                    provider=PaymentProvider.PAYPAL,
                    status=SubscriptionStatus.ACTIVE,
                    current_period_end=current_period_end
                )
                db.add(subscription)
            
            await db.commit()
            logger.success(f"Subscription activated in DB: {subscription_id}")
            
    except Exception as e:
        logger.error(f"Error handling BILLING.SUBSCRIPTION.ACTIVATED: {str(e)}")
        raise


async def handle_subscription_cancelled(event_data: dict) -> None:
    """
    Handle BILLING.SUBSCRIPTION.CANCELLED webhook event.
    
    Marks subscription as canceled in database.
    
    Args:
        event_data: PayPal event resource object
    """
    try:
        subscription_id = event_data.get("id")
        custom_id = event_data.get("custom_id")
        
        logger.info(f"Processing BILLING.SUBSCRIPTION.CANCELLED: {subscription_id}")
        
        if not custom_id:
            logger.warning(f"No custom_id (group_id) in subscription: {subscription_id}")
            return
        
        group_id = custom_id
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if subscription:
                subscription.status = SubscriptionStatus.CANCELED
                await db.commit()
                logger.success(f"Subscription marked as canceled: {subscription_id}")
            
    except Exception as e:
        logger.error(f"Error handling BILLING.SUBSCRIPTION.CANCELLED: {str(e)}")
        raise


async def handle_subscription_suspended(event_data: dict) -> None:
    """
    Handle BILLING.SUBSCRIPTION.SUSPENDED webhook event.
    
    Marks subscription as inactive when suspended (e.g., payment failure).
    
    Args:
        event_data: PayPal event resource object
    """
    try:
        subscription_id = event_data.get("id")
        custom_id = event_data.get("custom_id")
        
        logger.info(f"Processing BILLING.SUBSCRIPTION.SUSPENDED: {subscription_id}")
        
        if not custom_id:
            logger.warning(f"No custom_id (group_id) in subscription: {subscription_id}")
            return
        
        group_id = custom_id
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if subscription:
                subscription.status = SubscriptionStatus.INACTIVE
                await db.commit()
                logger.success(f"Subscription marked as inactive: {subscription_id}")
            
    except Exception as e:
        logger.error(f"Error handling BILLING.SUBSCRIPTION.SUSPENDED: {str(e)}")
        raise


async def handle_subscription_updated(event_data: dict) -> None:
    """
    Handle BILLING.SUBSCRIPTION.UPDATED webhook event.
    
    Updates subscription details when modified.
    
    Args:
        event_data: PayPal event resource object
    """
    try:
        subscription_id = event_data.get("id")
        custom_id = event_data.get("custom_id")
        status = event_data.get("status")
        
        logger.info(f"Processing BILLING.SUBSCRIPTION.UPDATED: {subscription_id}")
        
        if not custom_id:
            logger.warning(f"No custom_id (group_id) in subscription: {subscription_id}")
            return
        
        group_id = custom_id
        
        # Map PayPal status to our status
        status_mapping = {
            "ACTIVE": SubscriptionStatus.ACTIVE,
            "SUSPENDED": SubscriptionStatus.INACTIVE,
            "CANCELLED": SubscriptionStatus.CANCELED,
            "EXPIRED": SubscriptionStatus.CANCELED
        }
        
        db_status = status_mapping.get(status, SubscriptionStatus.INACTIVE)
        
        # Get billing info
        billing_info = event_data.get("billing_info", {})
        next_billing_time = billing_info.get("next_billing_time")
        
        current_period_end = None
        if next_billing_time:
            current_period_end = datetime.fromisoformat(
                next_billing_time.replace('Z', '+00:00')
            )
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if subscription:
                subscription.status = db_status
                if current_period_end:
                    subscription.current_period_end = current_period_end
                await db.commit()
                logger.success(f"Subscription updated: {subscription_id}, status={db_status}")
            
    except Exception as e:
        logger.error(f"Error handling BILLING.SUBSCRIPTION.UPDATED: {str(e)}")
        raise


async def handle_payment_sale_completed(event_data: dict) -> None:
    """
    Handle PAYMENT.SALE.COMPLETED webhook event.
    
    Logs successful payment and ensures subscription is active.
    
    Args:
        event_data: PayPal event resource object
    """
    try:
        sale_id = event_data.get("id")
        billing_agreement_id = event_data.get("billing_agreement_id")
        
        logger.info(f"Processing PAYMENT.SALE.COMPLETED: {sale_id}")
        
        if billing_agreement_id:
            # Get subscription details to find group_id
            try:
                subscription_details = await get_subscription_details(billing_agreement_id)
                custom_id = subscription_details.get("custom_id")
                
                if custom_id:
                    group_id = custom_id
                    
                    async with AsyncSessionLocal() as db:
                        result = await db.execute(
                            select(Subscription).where(Subscription.group_id == group_id)
                        )
                        subscription = result.scalar_one_or_none()
                        
                        if subscription:
                            subscription.status = SubscriptionStatus.ACTIVE
                            await db.commit()
                            logger.success(f"Subscription activated after payment: {sale_id}")
            except Exception as e:
                logger.warning(f"Could not update subscription for sale {sale_id}: {str(e)}")
        
    except Exception as e:
        logger.error(f"Error handling PAYMENT.SALE.COMPLETED: {str(e)}")
        raise


async def process_webhook_event(event: dict) -> bool:
    """
    Process PayPal webhook event and update database.
    
    Routes events to appropriate handlers based on event type.
    
    Args:
        event: PayPal webhook event dictionary
        
    Returns:
        True if processed successfully, False otherwise
        
    Usage:
        result = await verify_webhook(payload, headers)
        if result["verification_status"] == "SUCCESS":
            success = await process_webhook_event(payload)
    """
    event_type = event.get("event_type")
    resource = event.get("resource", {})
    
    logger.info(f"Processing webhook event: {event_type}")
    
    try:
        if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
            await handle_subscription_activated(resource)
        elif event_type == "BILLING.SUBSCRIPTION.CANCELLED":
            await handle_subscription_cancelled(resource)
        elif event_type == "BILLING.SUBSCRIPTION.SUSPENDED":
            await handle_subscription_suspended(resource)
        elif event_type == "BILLING.SUBSCRIPTION.UPDATED":
            await handle_subscription_updated(resource)
        elif event_type == "BILLING.SUBSCRIPTION.EXPIRED":
            await handle_subscription_cancelled(resource)
        elif event_type == "PAYMENT.SALE.COMPLETED":
            await handle_payment_sale_completed(resource)
        elif event_type == "PAYMENT.SALE.REFUNDED":
            logger.warning(f"Payment refunded: {resource.get('id')}")
        else:
            logger.info(f"Unhandled event type: {event_type}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error processing webhook event {event_type}: {str(e)}")
        return False
