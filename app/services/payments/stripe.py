"""
CORELINK Stripe Payment Integration

Handles subscription management and webhook processing for Stripe payments.
"""

import stripe
from typing import Any
from datetime import datetime
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Subscription, Group
from app.models.subscription import PaymentProvider, SubscriptionStatus


# Configure Stripe API key
stripe.api_key = settings.STRIPE_SECRET_KEY


async def create_subscription(customer_id: str, price_id: str) -> dict[str, Any]:
    """
    Create a new Stripe subscription.
    
    Creates a subscription for a customer with the specified price ID.
    
    Args:
        customer_id: Stripe customer ID (e.g., "cus_...")
        price_id: Stripe price ID (e.g., "price_...")
        
    Returns:
        Dictionary with subscription data:
        {
            "subscription_id": "sub_...",
            "status": "active",
            "current_period_end": 1234567890,
            "customer_id": "cus_...",
            "price_id": "price_..."
        }
        
    Raises:
        stripe.error.StripeError: If Stripe API call fails
        
    Usage:
        from app.services.payments.stripe import create_subscription
        
        subscription = await create_subscription(
            customer_id="cus_abc123",
            price_id="price_xyz789"
        )
    """
    try:
        logger.info(f"Creating Stripe subscription for customer: {customer_id}")
        
        # Create subscription via Stripe API (sync call in executor)
        import asyncio
        loop = asyncio.get_event_loop()
        
        subscription = await loop.run_in_executor(
            None,
            lambda: stripe.Subscription.create(
                customer=customer_id,
                items=[{"price": price_id}],
                payment_behavior="default_incomplete",
                expand=["latest_invoice.payment_intent"]
            )
        )
        
        result = {
            "subscription_id": subscription.id,
            "status": subscription.status,
            "current_period_end": subscription.current_period_end,
            "customer_id": subscription.customer,
            "price_id": price_id,
            "client_secret": None
        }
        
        # Extract client secret if available
        if (hasattr(subscription, 'latest_invoice') and 
            subscription.latest_invoice and
            hasattr(subscription.latest_invoice, 'payment_intent') and
            subscription.latest_invoice.payment_intent):
            result["client_secret"] = subscription.latest_invoice.payment_intent.client_secret
        
        logger.success(
            f"Subscription created: {subscription.id}, "
            f"status={subscription.status}"
        )
        
        return result
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe subscription creation failed: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error creating subscription: {str(e)}")
        raise


async def cancel_subscription(subscription_id: str) -> dict[str, Any]:
    """
    Cancel a Stripe subscription.
    
    Cancels the subscription at the end of the current billing period.
    
    Args:
        subscription_id: Stripe subscription ID (e.g., "sub_...")
        
    Returns:
        Dictionary with cancellation data:
        {
            "subscription_id": "sub_...",
            "status": "canceled",
            "canceled_at": 1234567890
        }
        
    Raises:
        stripe.error.StripeError: If Stripe API call fails
        
    Usage:
        from app.services.payments.stripe import cancel_subscription
        
        result = await cancel_subscription("sub_abc123")
    """
    try:
        logger.info(f"Canceling Stripe subscription: {subscription_id}")
        
        # Cancel subscription via Stripe API (sync call in executor)
        import asyncio
        loop = asyncio.get_event_loop()
        
        subscription = await loop.run_in_executor(
            None,
            lambda: stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True
            )
        )
        
        result = {
            "subscription_id": subscription.id,
            "status": subscription.status,
            "canceled_at": subscription.canceled_at,
            "cancel_at_period_end": subscription.cancel_at_period_end
        }
        
        logger.success(f"Subscription canceled: {subscription_id}")
        
        return result
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe subscription cancellation failed: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error canceling subscription: {str(e)}")
        raise


def verify_webhook_signature(
    payload: str,
    sig_header: str
) -> dict[str, Any]:
    """
    Verify Stripe webhook signature and parse event.
    
    Validates that the webhook request came from Stripe using
    the webhook signing secret.
    
    Args:
        payload: Raw request body as string
        sig_header: Value of Stripe-Signature header
        
    Returns:
        Parsed Stripe event dictionary
        
    Raises:
        stripe.error.SignatureVerificationError: If signature is invalid
        ValueError: If payload is invalid
        
    Usage:
        from app.services.payments.stripe import verify_webhook_signature
        
        @router.post("/webhook/stripe")
        async def stripe_webhook(request: Request):
            payload = await request.body()
            sig_header = request.headers.get("stripe-signature")
            
            event = verify_webhook_signature(
                payload.decode(),
                sig_header
            )
    """
    try:
        logger.debug("Verifying Stripe webhook signature")
        
        # Verify signature and construct event
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            settings.STRIPE_WEBHOOK_SECRET
        )
        
        logger.success(f"Webhook verified: {event['type']}")
        return event
        
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Stripe webhook signature verification failed: {str(e)}")
        raise
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error verifying webhook: {str(e)}")
        raise


async def handle_subscription_created(event_data: dict) -> None:
    """
    Handle subscription.created webhook event.
    
    Creates or updates subscription record in database.
    
    Args:
        event_data: Stripe event data object
    """
    try:
        subscription_obj = event_data['object']
        subscription_id = subscription_obj['id']
        customer_id = subscription_obj['customer']
        status = subscription_obj['status']
        current_period_end = subscription_obj['current_period_end']
        
        logger.info(f"Processing subscription.created: {subscription_id}")
        
        async with AsyncSessionLocal() as db:
            # Find group by customer_id (stored in metadata)
            metadata = subscription_obj.get('metadata', {})
            group_id = metadata.get('group_id')
            
            if not group_id:
                logger.warning(f"No group_id in subscription metadata: {subscription_id}")
                return
            
            # Get or create subscription record
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if subscription:
                # Update existing
                subscription.provider = PaymentProvider.STRIPE
                subscription.status = SubscriptionStatus.ACTIVE if status == "active" else SubscriptionStatus.INACTIVE
                subscription.current_period_end = datetime.fromtimestamp(current_period_end)
            else:
                # Create new
                subscription = Subscription(
                    group_id=group_id,
                    provider=PaymentProvider.STRIPE,
                    status=SubscriptionStatus.ACTIVE if status == "active" else SubscriptionStatus.INACTIVE,
                    current_period_end=datetime.fromtimestamp(current_period_end)
                )
                db.add(subscription)
            
            await db.commit()
            logger.success(f"Subscription record updated: {subscription_id}")

            # Post-payment: activate group and create group admin dashboard account
            if status == "active":
                subscriber_email = subscription_obj.get("metadata", {}).get("email", "")
                if not subscriber_email:
                    subscriber_email = metadata.get("subscriber_email", "")
                if subscriber_email:
                    from app.services.post_payment import provision_group_after_payment
                    await provision_group_after_payment(
                        db=db,
                        group_id=group_id,
                        subscriber_email=subscriber_email,
                    )
            
    except Exception as e:
        logger.error(f"Error handling subscription.created: {str(e)}")
        raise


async def handle_subscription_updated(event_data: dict) -> None:
    """
    Handle subscription.updated webhook event.
    
    Updates subscription record in database.
    
    Args:
        event_data: Stripe event data object
    """
    try:
        subscription_obj = event_data['object']
        subscription_id = subscription_obj['id']
        status = subscription_obj['status']
        current_period_end = subscription_obj['current_period_end']
        
        logger.info(f"Processing subscription.updated: {subscription_id}")
        
        async with AsyncSessionLocal() as db:
            # Find subscription by group_id from metadata
            metadata = subscription_obj.get('metadata', {})
            group_id = metadata.get('group_id')
            
            if not group_id:
                logger.warning(f"No group_id in subscription metadata: {subscription_id}")
                return
            
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if not subscription:
                logger.warning(f"Subscription not found for group: {group_id}")
                return
            
            # Update status
            if status == "active":
                subscription.status = SubscriptionStatus.ACTIVE
            elif status == "canceled":
                subscription.status = SubscriptionStatus.CANCELED
            else:
                subscription.status = SubscriptionStatus.INACTIVE
            
            subscription.current_period_end = datetime.fromtimestamp(current_period_end)
            
            await db.commit()
            logger.success(f"Subscription updated: {subscription_id}, status={status}")
            
    except Exception as e:
        logger.error(f"Error handling subscription.updated: {str(e)}")
        raise


async def handle_subscription_deleted(event_data: dict) -> None:
    """
    Handle subscription.deleted webhook event.
    
    Marks subscription as canceled in database.
    
    Args:
        event_data: Stripe event data object
    """
    try:
        subscription_obj = event_data['object']
        subscription_id = subscription_obj['id']
        
        logger.info(f"Processing subscription.deleted: {subscription_id}")
        
        async with AsyncSessionLocal() as db:
            metadata = subscription_obj.get('metadata', {})
            group_id = metadata.get('group_id')
            
            if not group_id:
                logger.warning(f"No group_id in subscription metadata: {subscription_id}")
                return
            
            result = await db.execute(
                select(Subscription).where(Subscription.group_id == group_id)
            )
            subscription = result.scalar_one_or_none()
            
            if subscription:
                subscription.status = SubscriptionStatus.CANCELED
                await db.commit()
                logger.success(f"Subscription marked as canceled: {subscription_id}")
            
    except Exception as e:
        logger.error(f"Error handling subscription.deleted: {str(e)}")
        raise


async def process_webhook_event(event: dict) -> bool:
    """
    Process Stripe webhook event and update database.
    
    Routes events to appropriate handlers based on event type.
    
    Args:
        event: Verified Stripe event dictionary
        
    Returns:
        True if processed successfully, False otherwise
        
    Usage:
        event = verify_webhook_signature(payload, sig_header)
        success = await process_webhook_event(event)
    """
    event_type = event['type']
    event_data = event['data']
    
    logger.info(f"Processing webhook event: {event_type}")
    
    try:
        if event_type == 'customer.subscription.created':
            await handle_subscription_created(event_data)
        elif event_type == 'customer.subscription.updated':
            await handle_subscription_updated(event_data)
        elif event_type == 'customer.subscription.deleted':
            await handle_subscription_deleted(event_data)
        elif event_type == 'invoice.payment_succeeded':
            logger.info(f"Payment succeeded for invoice: {event_data['object']['id']}")
        elif event_type == 'invoice.payment_failed':
            logger.warning(f"Payment failed for invoice: {event_data['object']['id']}")
        else:
            logger.info(f"Unhandled event type: {event_type}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error processing webhook event {event_type}: {str(e)}")
        return False


async def create_customer(email: str, group_id: str) -> dict[str, Any]:
    """
    Create a Stripe customer.
    
    Args:
        email: Customer email
        group_id: Group UUID to store in metadata
        
    Returns:
        Dictionary with customer data
    """
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        
        customer = await loop.run_in_executor(
            None,
            lambda: stripe.Customer.create(
                email=email,
                metadata={"group_id": group_id}
            )
        )
        
        return {
            "customer_id": customer.id,
            "email": customer.email
        }
        
    except stripe.error.StripeError as e:
        logger.error(f"Failed to create Stripe customer: {str(e)}")
        raise
