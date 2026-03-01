"""
CORELINK Paystack Payment Integration

Handles subscription management and webhook processing for Paystack payments.
"""

import hmac
import hashlib
import httpx
from typing import Any
from datetime import datetime
from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Subscription, Group
from app.models.subscription import PaymentProvider, SubscriptionStatus


# Paystack API base URL
PAYSTACK_BASE_URL = "https://api.paystack.co"


async def create_subscription(email: str, plan_code: str) -> dict[str, Any]:
    """
    Create a new Paystack subscription.
    
    Initializes a subscription for a customer with the specified plan.
    
    Args:
        email: Customer email address
        plan_code: Paystack plan code (e.g., "PLN_...")
        
    Returns:
        Dictionary with subscription data:
        {
            "subscription_code": "SUB_...",
            "email_token": "...",
            "authorization_url": "https://...",
            "access_code": "..."
        }
        
    Raises:
        httpx.HTTPError: If Paystack API call fails
        
    Usage:
        from app.services.payments.paystack import create_subscription
        
        subscription = await create_subscription(
            email="user@example.com",
            plan_code="PLN_abc123"
        )
    """
    try:
        logger.info(f"Creating Paystack subscription for: {email}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{PAYSTACK_BASE_URL}/subscription",
                headers={
                    "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "customer": email,
                    "plan": plan_code
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            if not data.get("status"):
                raise Exception(f"Paystack API error: {data.get('message')}")
            
            result_data = data["data"]
            
            result = {
                "subscription_code": result_data.get("subscription_code"),
                "email_token": result_data.get("email_token"),
                "authorization_url": result_data.get("authorization_url"),
                "access_code": result_data.get("access_code")
            }
            
            logger.success(
                f"Subscription created: {result['subscription_code']}"
            )
            
            return result
            
    except httpx.HTTPError as e:
        logger.error(f"Paystack HTTP error: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error creating subscription: {str(e)}")
        raise


async def verify_transaction(reference: str) -> dict[str, Any]:
    """
    Verify a Paystack transaction.
    
    Confirms that a payment transaction was successful.
    
    Args:
        reference: Paystack transaction reference
        
    Returns:
        Dictionary with transaction data:
        {
            "status": "success",
            "amount": 50000,
            "currency": "NGN",
            "customer_email": "user@example.com",
            "paid_at": "2023-01-15T10:00:00.000Z"
        }
        
    Raises:
        httpx.HTTPError: If Paystack API call fails
        
    Usage:
        from app.services.payments.paystack import verify_transaction
        
        transaction = await verify_transaction("ref_abc123")
        if transaction["status"] == "success":
            # Activate subscription
    """
    try:
        logger.info(f"Verifying Paystack transaction: {reference}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{PAYSTACK_BASE_URL}/transaction/verify/{reference}",
                headers={
                    "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            if not data.get("status"):
                raise Exception(f"Paystack API error: {data.get('message')}")
            
            transaction_data = data["data"]
            
            result = {
                "status": transaction_data.get("status"),
                "amount": transaction_data.get("amount"),
                "currency": transaction_data.get("currency"),
                "customer_email": transaction_data.get("customer", {}).get("email"),
                "paid_at": transaction_data.get("paid_at"),
                "reference": transaction_data.get("reference"),
                "metadata": transaction_data.get("metadata", {})
            }
            
            logger.success(
                f"Transaction verified: {reference}, "
                f"status={result['status']}"
            )
            
            return result
            
    except httpx.HTTPError as e:
        logger.error(f"Paystack HTTP error: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error verifying transaction: {str(e)}")
        raise


def verify_webhook_signature(payload: str, signature: str) -> bool:
    """
    Verify Paystack webhook signature.
    
    Validates that the webhook request came from Paystack using
    HMAC SHA-512 signature verification.
    
    Args:
        payload: Raw request body as string
        signature: Value of x-paystack-signature header
        
    Returns:
        True if signature is valid, False otherwise
        
    Usage:
        from app.services.payments.paystack import verify_webhook_signature
        
        @router.post("/webhook/paystack")
        async def paystack_webhook(request: Request):
            payload = await request.body()
            signature = request.headers.get("x-paystack-signature")
            
            if not verify_webhook_signature(payload.decode(), signature):
                raise HTTPException(status_code=401)
    """
    try:
        logger.debug("Verifying Paystack webhook signature")
        
        # Compute HMAC SHA-512
        computed_signature = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        
        # Compare signatures (constant-time comparison)
        is_valid = hmac.compare_digest(computed_signature, signature)
        
        if is_valid:
            logger.success("Webhook signature verified")
        else:
            logger.warning("Invalid webhook signature")
        
        return is_valid
        
    except Exception as e:
        logger.error(f"Error verifying webhook signature: {str(e)}")
        return False


async def handle_subscription_create(event_data: dict) -> None:
    """
    Handle subscription.create webhook event.
    
    Creates or updates subscription record in database.
    
    Args:
        event_data: Paystack event data object
    """
    try:
        subscription_code = event_data.get("subscription_code")
        customer_email = event_data.get("customer", {}).get("email")
        status = event_data.get("status")
        
        logger.info(f"Processing subscription.create: {subscription_code}")
        
        async with AsyncSessionLocal() as db:
            # Get group_id from metadata
            metadata = event_data.get("metadata", {})
            group_id = metadata.get("group_id")
            
            if not group_id:
                logger.warning(f"No group_id in subscription metadata: {subscription_code}")
                return
            
            # Get next payment date (if available)
            next_payment_date = event_data.get("next_payment_date")
            current_period_end = None
            if next_payment_date:
                current_period_end = datetime.fromisoformat(
                    next_payment_date.replace('Z', '+00:00')
                )
            
            # Get or create subscription record
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if subscription:
                # Update existing
                subscription.provider = PaymentProvider.PAYSTACK
                subscription.status = SubscriptionStatus.ACTIVE if status == "active" else SubscriptionStatus.INACTIVE
                subscription.current_period_end = current_period_end
            else:
                # Create new
                subscription = Subscription(
                    group_id=group_id,
                    provider=PaymentProvider.PAYSTACK,
                    status=SubscriptionStatus.ACTIVE if status == "active" else SubscriptionStatus.INACTIVE,
                    current_period_end=current_period_end
                )
                db.add(subscription)
            
            await db.commit()
            logger.success(f"Subscription record updated: {subscription_code}")

            # Post-payment: activate group and create group admin dashboard account
            if status == "active":
                subscriber_email = event_data.get("customer", {}).get("email", "")
                if subscriber_email and group_id:
                    from app.services.post_payment import provision_group_after_payment
                    await provision_group_after_payment(
                        db=db,
                        group_id=group_id,
                        subscriber_email=subscriber_email,
                    )
            
    except Exception as e:
        logger.error(f"Error handling subscription.create: {str(e)}")
        raise


async def handle_subscription_disable(event_data: dict) -> None:
    """
    Handle subscription.disable webhook event.
    
    Marks subscription as canceled in database.
    
    Args:
        event_data: Paystack event data object
    """
    try:
        subscription_code = event_data.get("subscription_code")
        
        logger.info(f"Processing subscription.disable: {subscription_code}")
        
        async with AsyncSessionLocal() as db:
            metadata = event_data.get("metadata", {})
            group_id = metadata.get("group_id")
            
            if not group_id:
                logger.warning(f"No group_id in subscription metadata: {subscription_code}")
                return
            
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if subscription:
                subscription.status = SubscriptionStatus.CANCELED
                await db.commit()
                logger.success(f"Subscription marked as canceled: {subscription_code}")
            
    except Exception as e:
        logger.error(f"Error handling subscription.disable: {str(e)}")
        raise


async def handle_charge_success(event_data: dict) -> None:
    """
    Handle charge.success webhook event.
    
    Updates subscription status when payment succeeds.
    
    Args:
        event_data: Paystack event data object
    """
    try:
        reference = event_data.get("reference")
        customer_email = event_data.get("customer", {}).get("email")
        
        logger.info(f"Processing charge.success: {reference}")
        
        # Extract group_id from metadata
        metadata = event_data.get("metadata", {})
        group_id = metadata.get("group_id")
        
        if group_id:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Subscription).where(Subscription.group_id == group_id)
                )
                subscription = result.scalar_one_or_none()
                
                if subscription:
                    subscription.status = SubscriptionStatus.ACTIVE
                    await db.commit()
                    logger.success(f"Subscription activated after payment: {reference}")
        
    except Exception as e:
        logger.error(f"Error handling charge.success: {str(e)}")
        raise


async def process_webhook_event(event: dict) -> bool:
    """
    Process Paystack webhook event and update database.
    
    Routes events to appropriate handlers based on event type.
    
    Args:
        event: Paystack event dictionary
        
    Returns:
        True if processed successfully, False otherwise
        
    Usage:
        if verify_webhook_signature(payload, signature):
            success = await process_webhook_event(event_data)
    """
    event_type = event.get("event")
    event_data = event.get("data", {})
    
    logger.info(f"Processing webhook event: {event_type}")
    
    try:
        if event_type == 'subscription.create':
            await handle_subscription_create(event_data)
        elif event_type == 'subscription.disable':
            await handle_subscription_disable(event_data)
        elif event_type == 'subscription.not_renew':
            await handle_subscription_disable(event_data)
        elif event_type == 'charge.success':
            await handle_charge_success(event_data)
        elif event_type == 'invoice.create':
            logger.info(f"Invoice created: {event_data.get('invoice_code')}")
        elif event_type == 'invoice.payment_failed':
            logger.warning(f"Invoice payment failed: {event_data.get('invoice_code')}")
        else:
            logger.info(f"Unhandled event type: {event_type}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error processing webhook event {event_type}: {str(e)}")
        return False


async def get_subscription_details(subscription_code: str) -> dict[str, Any]:
    """
    Get details of a Paystack subscription.
    
    Args:
        subscription_code: Paystack subscription code
        
    Returns:
        Dictionary with subscription details
    """
    try:
        logger.info(f"Fetching Paystack subscription: {subscription_code}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{PAYSTACK_BASE_URL}/subscription/{subscription_code}",
                headers={
                    "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            if not data.get("status"):
                raise Exception(f"Paystack API error: {data.get('message')}")
            
            return data["data"]
            
    except httpx.HTTPError as e:
        logger.error(f"Paystack HTTP error: {str(e)}")
        raise


async def cancel_subscription(subscription_code: str, email_token: str) -> dict[str, Any]:
    """
    Cancel a Paystack subscription.
    
    Args:
        subscription_code: Paystack subscription code
        email_token: Email token for the subscription
        
    Returns:
        Dictionary with cancellation result
    """
    try:
        logger.info(f"Canceling Paystack subscription: {subscription_code}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{PAYSTACK_BASE_URL}/subscription/disable",
                headers={
                    "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "code": subscription_code,
                    "token": email_token
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            if not data.get("status"):
                raise Exception(f"Paystack API error: {data.get('message')}")
            
            logger.success(f"Subscription canceled: {subscription_code}")
            
            return {"message": data.get("message"), "subscription_code": subscription_code}
            
    except httpx.HTTPError as e:
        logger.error(f"Paystack HTTP error: {str(e)}")
        raise
