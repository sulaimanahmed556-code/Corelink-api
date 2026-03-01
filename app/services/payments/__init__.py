"""
CORELINK Payment Services

Exports payment gateway integrations.
"""

from app.services.payments.stripe import (
    create_subscription as stripe_create_subscription,
    cancel_subscription as stripe_cancel_subscription,
    verify_webhook_signature as stripe_verify_webhook,
    process_webhook_event as stripe_process_webhook,
    create_customer as stripe_create_customer,
)

from app.services.payments.paystack import (
    create_subscription as paystack_create_subscription,
    verify_transaction as paystack_verify_transaction,
    verify_webhook_signature as paystack_verify_webhook,
    process_webhook_event as paystack_process_webhook,
    get_subscription_details as paystack_get_subscription,
    cancel_subscription as paystack_cancel_subscription,
)

from app.services.payments.paypal import (
    create_subscription as paypal_create_subscription,
    cancel_subscription as paypal_cancel_subscription,
    verify_webhook as paypal_verify_webhook,
    process_webhook_event as paypal_process_webhook,
    get_subscription_details as paypal_get_subscription,
    get_access_token as paypal_get_access_token,
)

__all__ = [
    # Stripe
    "stripe_create_subscription",
    "stripe_cancel_subscription",
    "stripe_verify_webhook",
    "stripe_process_webhook",
    "stripe_create_customer",
    
    # Paystack
    "paystack_create_subscription",
    "paystack_verify_transaction",
    "paystack_verify_webhook",
    "paystack_process_webhook",
    "paystack_get_subscription",
    "paystack_cancel_subscription",
    
    # PayPal
    "paypal_create_subscription",
    "paypal_cancel_subscription",
    "paypal_verify_webhook",
    "paypal_process_webhook",
    "paypal_get_subscription",
    "paypal_get_access_token",
]
