"""
Plan Provisioner Service

When an admin creates a plan, this service automatically creates
the corresponding plan/product on Stripe, Paystack, and PayPal,
then returns the provider-specific IDs to store on the Plan record.
"""

import asyncio
from decimal import Decimal
from typing import Any

from loguru import logger


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------

async def create_stripe_plan(
    name: str,
    price: Decimal,
    currency: str,
    interval: str = "month",
    interval_count: int = 1,
) -> str | None:
    """
    Create a Stripe Product + Price and return the Price ID (e.g. price_xxx).
    Returns None on failure so the rest of the plan can still be saved.
    """
    try:
        import stripe
        from app.config import settings

        stripe.api_key = settings.STRIPE_SECRET_KEY

        loop = asyncio.get_event_loop()

        # Create product
        product = await loop.run_in_executor(
            None,
            lambda: stripe.Product.create(name=name),
        )

        # Amount in smallest currency unit (cents for USD)
        unit_amount = int(price * 100)

        price_obj = await loop.run_in_executor(
            None,
            lambda: stripe.Price.create(
                unit_amount=unit_amount,
                currency=currency.lower(),
                recurring={"interval": interval, "interval_count": interval_count},
                product=product["id"],
            ),
        )

        logger.info(f"Stripe plan created: {price_obj['id']}")
        return price_obj["id"]

    except Exception as exc:
        logger.warning(f"Failed to create Stripe plan: {exc}")
        return None


# ---------------------------------------------------------------------------
# Paystack
# ---------------------------------------------------------------------------

async def create_paystack_plan(
    name: str,
    price: Decimal,
    currency: str,
    interval: str = "monthly",
) -> str | None:
    """
    Create a Paystack plan and return the plan_code.
    Interval must be: daily | weekly | monthly | annually | biannually | quarterly
    """
    try:
        import httpx
        from app.config import settings

        # Map generic intervals to Paystack values
        interval_map = {
            "month": "monthly",
            "monthly": "monthly",
            "year": "annually",
            "annually": "annually",
            "week": "weekly",
            "weekly": "weekly",
            "day": "daily",
            "daily": "daily",
        }
        paystack_interval = interval_map.get(interval, "monthly")

        # Paystack expects amount in kobo (NGN) or cents (USD) — smallest unit
        amount = int(price * 100)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.paystack.co/plan",
                headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
                json={
                    "name": name,
                    "interval": paystack_interval,
                    "amount": amount,
                    "currency": currency.upper(),
                },
                timeout=15,
            )
            data = response.json()

        if data.get("status"):
            code = data["data"]["plan_code"]
            logger.info(f"Paystack plan created: {code}")
            return code
        else:
            logger.warning(f"Paystack plan creation failed: {data.get('message')}")
            return None

    except Exception as exc:
        logger.warning(f"Failed to create Paystack plan: {exc}")
        return None


# ---------------------------------------------------------------------------
# PayPal
# ---------------------------------------------------------------------------

async def create_paypal_plan(
    name: str,
    description: str | None,
    price: Decimal,
    currency: str,
    interval: str = "MONTH",
    interval_count: int = 1,
) -> str | None:
    """
    Create a PayPal billing product + plan and return the plan ID.
    """
    try:
        import httpx
        from app.services.payments.paypal import get_access_token, get_paypal_base_url

        base_url = get_paypal_base_url()
        access_token = await get_access_token()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        interval_map = {
            "month": "MONTH",
            "monthly": "MONTH",
            "year": "YEAR",
            "annually": "YEAR",
            "week": "WEEK",
            "day": "DAY",
        }
        paypal_interval = interval_map.get(interval.lower(), "MONTH")

        async with httpx.AsyncClient() as client:
            # Create product
            product_resp = await client.post(
                f"{base_url}/v1/catalogs/products",
                headers=headers,
                json={"name": name, "description": description or name, "type": "SERVICE"},
                timeout=15,
            )
            product_data = product_resp.json()

            if "id" not in product_data:
                logger.warning(f"PayPal product creation failed: {product_data}")
                return None

            product_id = product_data["id"]

            # Create billing plan
            plan_resp = await client.post(
                f"{base_url}/v1/billing/plans",
                headers=headers,
                json={
                    "product_id": product_id,
                    "name": name,
                    "description": description or name,
                    "billing_cycles": [
                        {
                            "frequency": {
                                "interval_unit": paypal_interval,
                                "interval_count": interval_count,
                            },
                            "tenure_type": "REGULAR",
                            "sequence": 1,
                            "total_cycles": 0,
                            "pricing_scheme": {
                                "fixed_price": {
                                    "value": str(price),
                                    "currency_code": currency.upper(),
                                }
                            },
                        }
                    ],
                    "payment_preferences": {
                        "auto_bill_outstanding": True,
                        "setup_fee_failure_action": "CONTINUE",
                        "payment_failure_threshold": 3,
                    },
                },
                timeout=15,
            )
            plan_data = plan_resp.json()

        if "id" in plan_data:
            logger.info(f"PayPal plan created: {plan_data['id']}")
            return plan_data["id"]
        else:
            logger.warning(f"PayPal plan creation failed: {plan_data}")
            return None

    except Exception as exc:
        logger.warning(f"Failed to create PayPal plan: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def provision_plan_on_all_providers(
    name: str,
    description: str | None,
    price: Decimal,
    currency: str,
    interval: str = "month",
    interval_count: int = 1,
) -> dict[str, Any]:
    """
    Create the plan on Stripe, Paystack, and PayPal concurrently.

    Returns:
        {
            "stripe_plan_id": "price_xxx" | None,
            "paystack_plan_code": "PLN_xxx" | None,
            "paypal_plan_id": "P-xxx" | None,
        }
    """
    stripe_id, paystack_code, paypal_id = await asyncio.gather(
        create_stripe_plan(name, price, currency, interval, interval_count),
        create_paystack_plan(name, price, currency, interval),
        create_paypal_plan(name, description, price, currency, interval, interval_count),
    )

    result = {
        "stripe_plan_id": stripe_id,
        "paystack_plan_code": paystack_code,
        "paypal_plan_id": paypal_id,
    }

    logger.info(f"Plan '{name}' provisioned on providers: {result}")
    return result
