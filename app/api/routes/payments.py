"""
CORELINK Payment Routes

Handles payment processing for subscriptions via multiple providers.
"""

from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Request, Response, status, HTTPException, Depends
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
import stripe

from app.services.payments.stripe import (
    verify_webhook_signature,
    process_webhook_event
)
from app.database import get_db
from app.models import Group, Plan, Subscription
from app.models.subscription import PaymentProvider, SubscriptionStatus

router = APIRouter()


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class CreateSubscriptionRequest(BaseModel):
    """Request model for creating a subscription."""
    
    group_id: str = Field(..., description="Group UUID")
    provider: PaymentProvider = Field(..., description="Payment provider (stripe, paystack, paypal)")
    email: str = Field(..., description="Subscriber email address")
    plan_db_id: Optional[str] = Field(
        None,
        description="Internal plan UUID created by admin",
    )
    customer_name: Optional[str] = Field(None, description="Customer full name")
    customer_info: Optional[dict[str, Any]] = Field(
        default_factory=dict,
        description="Additional customer details used by checkout client",
    )
    
    # Provider-specific fields
    price_id: Optional[str] = Field(None, description="Stripe price ID")
    plan_code: Optional[str] = Field(None, description="Paystack plan code")
    plan_id: Optional[str] = Field(None, description="PayPal plan ID")

    @field_validator("group_id", "plan_db_id", "price_id", "plan_code", "plan_id", mode="before")
    @classmethod
    def normalize_optional_ids(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class SubscriptionResponse(BaseModel):
    """Response model for subscription details."""
    
    group_id: str
    provider: PaymentProvider
    status: SubscriptionStatus
    current_period_end: Optional[str] = None
    created_at: str


class CreateSubscriptionResponse(BaseModel):
    """Response model for subscription creation."""
    
    status: str
    provider: PaymentProvider
    payment_url: Optional[str] = None
    client_secret: Optional[str] = None
    subscription_id: Optional[str] = None
    message: str


class CreatePlanRequest(BaseModel):
    """Request model for creating a billing plan."""

    name: str = Field(..., min_length=2, max_length=100, description="Plan name")
    description: Optional[str] = Field(None, description="Plan description")
    price: Decimal = Field(..., gt=0, description="Plan price")
    currency: str = Field(default="USD", min_length=3, max_length=10)
    stripe_plan_id: Optional[str] = Field(None, description="Stripe price/plan id")
    paypal_plan_id: Optional[str] = Field(None, description="PayPal plan id")
    paystack_plan_code: Optional[str] = Field(None, description="Paystack plan code")
    is_active: bool = Field(default=True, description="Whether plan is selectable")

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper().strip()

    @field_validator("stripe_plan_id", "paypal_plan_id", "paystack_plan_code")
    @classmethod
    def normalize_provider_value(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_provider_ids(self) -> "CreatePlanRequest":
        if not (self.stripe_plan_id or self.paypal_plan_id or self.paystack_plan_code):
            raise ValueError(
                "At least one of stripe_plan_id, paypal_plan_id, or paystack_plan_code is required"
            )
        return self


class UpdatePlanRequest(BaseModel):
    """Request model for updating a billing plan."""

    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    price: Optional[Decimal] = Field(None, gt=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=10)
    stripe_plan_id: Optional[str] = None
    paypal_plan_id: Optional[str] = None
    paystack_plan_code: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return value.strip()

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return value.upper().strip()

    @field_validator("stripe_plan_id", "paypal_plan_id", "paystack_plan_code")
    @classmethod
    def normalize_provider_value(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        cleaned = value.strip()
        return cleaned or None


class PlanResponse(BaseModel):
    """Response model for plan data."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: Optional[str] = None
    price: Decimal
    currency: str
    stripe_plan_id: Optional[str] = None
    paypal_plan_id: Optional[str] = None
    paystack_plan_code: Optional[str] = None
    is_active: bool
    created_at: str
    updated_at: str


class PlanListResponse(BaseModel):
    """Response model for listing plans."""

    total: int
    plans: list[PlanResponse]


def _parse_uuid(value: str, field_name: str) -> UUID:
    """Parse UUID string or raise 400."""
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}: must be a valid UUID",
        ) from exc


def _plan_to_response(plan: Plan) -> PlanResponse:
    """Convert SQLAlchemy Plan model to API response."""
    return PlanResponse(
        id=plan.id,
        name=plan.name,
        description=plan.description,
        price=plan.price,
        currency=plan.currency,
        stripe_plan_id=plan.stripe_plan_id,
        paypal_plan_id=plan.paypal_plan_id,
        paystack_plan_code=plan.paystack_plan_code,
        is_active=plan.is_active,
        created_at=plan.created_at.isoformat(),
        updated_at=plan.updated_at.isoformat(),
    )


async def _ensure_unique_provider_refs(
    db: AsyncSession,
    stripe_plan_id: Optional[str],
    paypal_plan_id: Optional[str],
    paystack_plan_code: Optional[str],
    exclude_plan_id: Optional[UUID] = None,
) -> None:
    """Ensure provider-specific identifiers are unique across plans."""
    conditions = []
    if stripe_plan_id:
        conditions.append(Plan.stripe_plan_id == stripe_plan_id)
    if paypal_plan_id:
        conditions.append(Plan.paypal_plan_id == paypal_plan_id)
    if paystack_plan_code:
        conditions.append(Plan.paystack_plan_code == paystack_plan_code)

    if not conditions:
        return

    query = select(Plan).where(or_(*conditions))
    if exclude_plan_id:
        query = query.where(Plan.id != exclude_plan_id)

    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "One or more provider identifiers are already assigned "
                "to another plan"
            ),
        )


# =============================================================================
# PLAN MANAGEMENT
# =============================================================================


@router.post("/plans", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    req: CreatePlanRequest,
    db: AsyncSession = Depends(get_db),
) -> PlanResponse:
    """
    Create a billing plan (admin endpoint).

    Note:
        Authentication/authorization should be added before production usage.
    """
    try:
        existing_name = await db.execute(select(Plan).where(Plan.name == req.name))
        if existing_name.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A plan with this name already exists",
            )

        await _ensure_unique_provider_refs(
            db=db,
            stripe_plan_id=req.stripe_plan_id,
            paypal_plan_id=req.paypal_plan_id,
            paystack_plan_code=req.paystack_plan_code,
        )

        plan = Plan(
            name=req.name,
            description=req.description,
            price=req.price,
            currency=req.currency,
            stripe_plan_id=req.stripe_plan_id,
            paypal_plan_id=req.paypal_plan_id,
            paystack_plan_code=req.paystack_plan_code,
            is_active=req.is_active,
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)

        return _plan_to_response(plan)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error creating plan: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create plan",
        ) from exc


@router.get("/plans", response_model=PlanListResponse)
async def list_plans(
    active_only: bool = True,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> PlanListResponse:
    """
    List plans for checkout or admin review.
    """
    try:
        query = select(Plan)
        if active_only:
            query = query.where(Plan.is_active.is_(True))

        query = query.order_by(Plan.price.asc(), Plan.created_at.desc())
        query = query.limit(limit).offset(offset)

        result = await db.execute(query)
        plans = result.scalars().all()

        return PlanListResponse(
            total=len(plans),
            plans=[_plan_to_response(plan) for plan in plans],
        )

    except Exception as exc:
        logger.error(f"Error listing plans: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list plans",
        ) from exc


@router.get("/plans/{plan_id}", response_model=PlanResponse)
async def get_plan(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
) -> PlanResponse:
    """Get a single plan by UUID."""
    plan_uuid = _parse_uuid(plan_id, "plan_id")

    result = await db.execute(select(Plan).where(Plan.id == plan_uuid))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found",
        )
    return _plan_to_response(plan)


@router.put("/plans/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: str,
    req: UpdatePlanRequest,
    db: AsyncSession = Depends(get_db),
) -> PlanResponse:
    """
    Update plan details (admin endpoint).
    """
    try:
        plan_uuid = _parse_uuid(plan_id, "plan_id")

        result = await db.execute(select(Plan).where(Plan.id == plan_uuid))
        plan = result.scalar_one_or_none()
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plan not found",
            )

        updates = req.model_dump(exclude_unset=True)
        if not updates:
            return _plan_to_response(plan)

        next_name = updates.get("name", plan.name)
        if next_name != plan.name:
            existing_name = await db.execute(select(Plan).where(Plan.name == next_name))
            if existing_name.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A plan with this name already exists",
                )

        next_stripe_plan_id = updates.get("stripe_plan_id", plan.stripe_plan_id)
        next_paypal_plan_id = updates.get("paypal_plan_id", plan.paypal_plan_id)
        next_paystack_plan_code = updates.get("paystack_plan_code", plan.paystack_plan_code)

        if not (next_stripe_plan_id or next_paypal_plan_id or next_paystack_plan_code):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "At least one of stripe_plan_id, paypal_plan_id, "
                    "or paystack_plan_code is required"
                ),
            )

        await _ensure_unique_provider_refs(
            db=db,
            stripe_plan_id=next_stripe_plan_id,
            paypal_plan_id=next_paypal_plan_id,
            paystack_plan_code=next_paystack_plan_code,
            exclude_plan_id=plan.id,
        )

        for key, value in updates.items():
            setattr(plan, key, value)

        await db.commit()
        await db.refresh(plan)
        return _plan_to_response(plan)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error updating plan {plan_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update plan",
        ) from exc


@router.delete("/plans/{plan_id}")
async def deactivate_plan(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Deactivate a plan without deleting it."""
    try:
        plan_uuid = _parse_uuid(plan_id, "plan_id")

        result = await db.execute(select(Plan).where(Plan.id == plan_uuid))
        plan = result.scalar_one_or_none()
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plan not found",
            )

        if not plan.is_active:
            return JSONResponse(
                content={
                    "status": "already_inactive",
                    "message": "Plan is already inactive",
                    "plan_id": str(plan.id),
                },
                status_code=status.HTTP_200_OK,
            )

        plan.is_active = False
        await db.commit()

        return JSONResponse(
            content={
                "status": "deactivated",
                "message": "Plan deactivated successfully",
                "plan_id": str(plan.id),
            },
            status_code=status.HTTP_200_OK,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error deactivating plan {plan_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to deactivate plan",
        ) from exc


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request) -> Response:
    """
    Handle Stripe webhook events.
    
    Stripe sends events for:
    - customer.subscription.created
    - customer.subscription.updated
    - customer.subscription.deleted
    - invoice.payment_succeeded
    - invoice.payment_failed
    
    Args:
        request: FastAPI request with Stripe event data
        
    Returns:
        200 OK response if processed successfully
        400 Bad Request if signature verification fails
    """
    try:
        # Get request body and signature header
        payload = await request.body()
        sig_header = request.headers.get("stripe-signature")
        
        if not sig_header:
            logger.warning("Missing Stripe signature header")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing signature header"
            )
        
        # Verify webhook signature
        event = verify_webhook_signature(
            payload.decode(),
            sig_header
        )
        
        # Process event and update database
        success = await process_webhook_event(event)
        
        if success:
            return Response(status_code=status.HTTP_200_OK)
        else:
            return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid Stripe signature: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature"
        )
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        # Return 200 to prevent Stripe from retrying
        return Response(status_code=status.HTTP_200_OK)


@router.post("/paystack/webhook")
async def paystack_webhook(request: Request) -> Response:
    """
    Handle Paystack webhook events.
    
    Paystack sends events for:
    - subscription.create
    - subscription.disable
    - subscription.not_renew
    - charge.success
    - invoice.create
    - invoice.payment_failed
    
    Args:
        request: FastAPI request with Paystack event data
        
    Returns:
        200 OK response if processed successfully
        401 Unauthorized if signature verification fails
    """
    try:
        # Import Paystack functions
        from app.services.payments.paystack import (
            verify_webhook_signature as paystack_verify_webhook,
            process_webhook_event as paystack_process_webhook
        )
        
        # Get request body and signature header
        payload = await request.body()
        signature = request.headers.get("x-paystack-signature")
        
        if not signature:
            logger.warning("Missing Paystack signature header")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing signature header"
            )
        
        # Verify webhook signature (HMAC SHA-512)
        is_valid = paystack_verify_webhook(
            payload.decode(),
            signature
        )
        
        if not is_valid:
            logger.warning("Invalid Paystack webhook signature")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature"
            )
        
        # Parse event
        import json
        event = json.loads(payload.decode())
        
        # Process event and update database
        success = await paystack_process_webhook(event)
        
        if success:
            return Response(status_code=status.HTTP_200_OK)
        else:
            return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Paystack webhook error: {str(e)}")
        # Return 200 to prevent Paystack from retrying
        return Response(status_code=status.HTTP_200_OK)


@router.post("/paypal/webhook")
async def paypal_webhook(request: Request) -> Response:
    """
    Handle PayPal webhook events.
    
    PayPal sends events for:
    - BILLING.SUBSCRIPTION.ACTIVATED
    - BILLING.SUBSCRIPTION.CANCELLED
    - BILLING.SUBSCRIPTION.SUSPENDED
    - BILLING.SUBSCRIPTION.UPDATED
    - PAYMENT.SALE.COMPLETED
    - PAYMENT.SALE.REFUNDED
    
    Args:
        request: FastAPI request with PayPal event data
        
    Returns:
        200 OK response if processed successfully
        401 Unauthorized if signature verification fails
    """
    try:
        # Import PayPal functions
        from app.services.payments.paypal import (
            verify_webhook as paypal_verify_webhook,
            process_webhook_event as paypal_process_webhook
        )
        
        # Get request body and headers
        payload = await request.json()
        headers = {
            "paypal-auth-algo": request.headers.get("paypal-auth-algo"),
            "paypal-cert-url": request.headers.get("paypal-cert-url"),
            "paypal-transmission-id": request.headers.get("paypal-transmission-id"),
            "paypal-transmission-sig": request.headers.get("paypal-transmission-sig"),
            "paypal-transmission-time": request.headers.get("paypal-transmission-time")
        }
        
        # Verify webhook signature
        try:
            verification_result = await paypal_verify_webhook(payload, headers)
            
            if verification_result["verification_status"] not in ["SUCCESS", "SKIPPED"]:
                logger.warning("PayPal webhook verification failed")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Webhook verification failed"
                )
        except Exception as e:
            logger.error(f"PayPal webhook verification error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Webhook verification failed"
            )
        
        # Process event and update database
        success = await paypal_process_webhook(payload)
        
        if success:
            return Response(status_code=status.HTTP_200_OK)
        else:
            return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PayPal webhook error: {str(e)}")
        # Return 200 to prevent PayPal from retrying
        return Response(status_code=status.HTTP_200_OK)


@router.post("/create-subscription", response_model=CreateSubscriptionResponse)
async def create_subscription(
    req: CreateSubscriptionRequest,
    db: AsyncSession = Depends(get_db),
) -> CreateSubscriptionResponse:
    """
    Create a new subscription for a group.
    
    Creates a subscription with the selected payment provider and returns
    the payment URL for the user to complete payment.
    
    Args:
        req: Subscription creation request
        db: Database session
        
    Returns:
        CreateSubscriptionResponse with payment URL and subscription details
        
    Raises:
        HTTPException: If group not found or subscription creation fails
    """
    try:
        logger.info(
            f"Creating subscription for group {req.group_id} via {req.provider}"
        )

        group_uuid = _parse_uuid(req.group_id, "group_id")

        selected_plan: Optional[Plan] = None
        if req.plan_db_id:
            plan_uuid = _parse_uuid(req.plan_db_id, "plan_db_id")
            plan_result = await db.execute(
                select(Plan).where(
                    Plan.id == plan_uuid,
                    Plan.is_active.is_(True),
                )
            )
            selected_plan = plan_result.scalar_one_or_none()
            if not selected_plan:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Selected plan was not found or is inactive",
                )

        # Resolve provider identifiers from request or selected plan
        resolved_stripe_price_id = req.price_id or (
            selected_plan.stripe_plan_id if selected_plan else None
        )
        resolved_paystack_plan_code = req.plan_code or (
            selected_plan.paystack_plan_code if selected_plan else None
        )
        resolved_paypal_plan_id = req.plan_id or (
            selected_plan.paypal_plan_id if selected_plan else None
        )
        
        # Validate group exists
        result = await db.execute(
            select(Group).where(Group.id == group_uuid)
        )
        group = result.scalar_one_or_none()
        
        if not group:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Group not found"
            )
        
        # Check if subscription already exists
        result = await db.execute(
            select(Subscription).where(Subscription.group_id == group_uuid)
        )
        existing_subscription = result.scalar_one_or_none()
        
        if existing_subscription and existing_subscription.status == SubscriptionStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Group already has an active subscription"
            )
        
        # Create subscription based on provider
        if req.provider == PaymentProvider.STRIPE:
            from app.services.payments.stripe import (
                create_customer,
                create_subscription as stripe_create_subscription
            )
            
            if not resolved_stripe_price_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Stripe requires price_id directly or from "
                        "the selected plan's stripe_plan_id"
                    ),
                )
            
            # Create Stripe customer
            customer = await create_customer(
                email=req.email,
                group_id=str(group_uuid),
            )
            
            # Create subscription
            subscription = await stripe_create_subscription(
                customer_id=customer["customer_id"],
                price_id=resolved_stripe_price_id,
            )
            
            return CreateSubscriptionResponse(
                status="pending",
                provider=PaymentProvider.STRIPE,
                payment_url=None,  # Stripe uses client_secret for payment
                client_secret=subscription.get("client_secret"),
                subscription_id=subscription["subscription_id"],
                message="Subscription created. Use client_secret to complete payment."
            )
            
        elif req.provider == PaymentProvider.PAYSTACK:
            from app.services.payments.paystack import (
                create_subscription as paystack_create_subscription
            )
            
            if not resolved_paystack_plan_code:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Paystack requires plan_code directly or from "
                        "the selected plan's paystack_plan_code"
                    ),
                )
            
            # Create subscription
            subscription = await paystack_create_subscription(
                email=req.email,
                plan_code=resolved_paystack_plan_code,
            )
            
            return CreateSubscriptionResponse(
                status="pending",
                provider=PaymentProvider.PAYSTACK,
                payment_url=subscription["authorization_url"],
                subscription_id=subscription["subscription_code"],
                message="Please complete payment at authorization_url"
            )
            
        elif req.provider == PaymentProvider.PAYPAL:
            from app.services.payments.paypal import (
                create_subscription as paypal_create_subscription
            )
            
            if not resolved_paypal_plan_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "PayPal requires plan_id directly or from "
                        "the selected plan's paypal_plan_id"
                    ),
                )
            
            # Create subscription
            subscription = await paypal_create_subscription(
                plan_id=resolved_paypal_plan_id,
                subscriber_email=req.email,
                group_id=str(group_uuid),
            )
            
            return CreateSubscriptionResponse(
                status="pending",
                provider=PaymentProvider.PAYPAL,
                payment_url=subscription["approval_url"],
                subscription_id=subscription["subscription_id"],
                message="Please complete payment at approval_url"
            )
        
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported provider: {req.provider}"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create subscription: {str(e)}"
        )


@router.get("/subscription/{group_id}", response_model=SubscriptionResponse)
async def get_subscription(
    group_id: str,
    db: AsyncSession = Depends(get_db),
) -> SubscriptionResponse:
    """
    Get subscription details for a group.
    
    Fetches subscription information including provider, status,
    and billing period from the database.
    
    Args:
        group_id: Group UUID
        db: Database session
        
    Returns:
        SubscriptionResponse with subscription details
        
    Raises:
        HTTPException: If subscription not found
    """
    try:
        logger.info(f"Fetching subscription for group {group_id}")
        group_uuid = _parse_uuid(group_id, "group_id")
        
        # Fetch subscription from database
        result = await db.execute(
            select(Subscription).where(Subscription.group_id == group_uuid)
        )
        subscription = result.scalar_one_or_none()
        
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription not found for this group"
            )
        
        return SubscriptionResponse(
            group_id=str(subscription.group_id),
            provider=subscription.provider,
            status=subscription.status,
            current_period_end=subscription.current_period_end.isoformat() if subscription.current_period_end else None,
            created_at=subscription.created_at.isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch subscription: {str(e)}"
        )


@router.post("/cancel-subscription/{group_id}")
async def cancel_subscription_route(
    group_id: str,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    Cancel a group's subscription.
    
    Cancels the subscription with the payment provider and updates
    the database status to CANCELED.
    
    Args:
        group_id: Group UUID
        db: Database session
        
    Returns:
        JSON with cancellation confirmation
        
    Raises:
        HTTPException: If subscription not found or cancellation fails
        
    Note:
        This endpoint requires the subscription ID to be stored in the database.
        For production, you should also validate group ownership/admin permissions.
    """
    try:
        logger.info(f"Canceling subscription for group {group_id}")
        group_uuid = _parse_uuid(group_id, "group_id")
        
        # Fetch subscription from database
        result = await db.execute(
            select(Subscription).where(Subscription.group_id == group_uuid)
        )
        subscription = result.scalar_one_or_none()
        
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription not found for this group"
            )
        
        if subscription.status == SubscriptionStatus.CANCELED:
            return JSONResponse(
                content={
                    "status": "already_canceled",
                    "message": "Subscription is already canceled",
                    "group_id": group_id
                },
                status_code=status.HTTP_200_OK
            )
        
        # Cancel subscription at provider
        # Note: This requires storing provider-specific subscription IDs
        # For now, we'll mark as canceled in DB and log a warning
        
        if subscription.provider == PaymentProvider.STRIPE:
            # TODO: Implement Stripe cancellation
            # from app.services.payments.stripe import cancel_subscription
            # result = await cancel_subscription(stripe_subscription_id)
            logger.warning("Stripe cancellation not yet implemented with stored subscription_id")
            
        elif subscription.provider == PaymentProvider.PAYSTACK:
            # TODO: Implement Paystack cancellation
            # from app.services.payments.paystack import cancel_subscription
            # result = await cancel_subscription(subscription_code, email_token)
            logger.warning("Paystack cancellation not yet implemented with stored subscription_id")
            
        elif subscription.provider == PaymentProvider.PAYPAL:
            # TODO: Implement PayPal cancellation
            # from app.services.payments.paypal import cancel_subscription
            # result = await cancel_subscription(paypal_subscription_id)
            logger.warning("PayPal cancellation not yet implemented with stored subscription_id")
        
        # Update database status
        subscription.status = SubscriptionStatus.CANCELED
        await db.commit()
        
        logger.success(f"Subscription canceled for group {group_id}")
        
        return JSONResponse(
            content={
                "status": "canceled",
                "message": "Subscription canceled successfully",
                "group_id": group_id,
                "provider": subscription.provider.value
            },
            status_code=status.HTTP_200_OK
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error canceling subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel subscription: {str(e)}"
        )


# =============================================================================
# ADDITIONAL ENDPOINTS
# =============================================================================

@router.get("/health")
async def payment_health_check() -> JSONResponse:
    """
    Payment system health check.
    
    Returns:
        JSON with service status
    """
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "payments",
            "providers": ["stripe", "paystack", "paypal"]
        },
        status_code=status.HTTP_200_OK
    )


@router.get("/subscriptions")
async def list_subscriptions(
    status_filter: Optional[SubscriptionStatus] = None,
    provider_filter: Optional[PaymentProvider] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """
    List all subscriptions with optional filtering.
    
    Args:
        status_filter: Filter by subscription status
        provider_filter: Filter by payment provider
        limit: Maximum number of results (default: 50)
        offset: Number of results to skip (default: 0)
        db: Database session
        
    Returns:
        JSON with list of subscriptions
    """
    try:
        logger.info(f"Listing subscriptions (status={status_filter}, provider={provider_filter})")
        
        # Build query
        query = select(Subscription)
        
        if status_filter:
            query = query.where(Subscription.status == status_filter)
        
        if provider_filter:
            query = query.where(Subscription.provider == provider_filter)
        
        query = query.limit(limit).offset(offset)
        
        # Execute query
        result = await db.execute(query)
        subscriptions = result.scalars().all()
        
        # Format response
        subscription_list = [
            {
                "group_id": str(sub.group_id),
                "provider": sub.provider.value,
                "status": sub.status.value,
                "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
                "created_at": sub.created_at.isoformat()
            }
            for sub in subscriptions
        ]
        
        return JSONResponse(
            content={
                "total": len(subscription_list),
                "limit": limit,
                "offset": offset,
                "subscriptions": subscription_list
            },
            status_code=status.HTTP_200_OK
        )
        
    except Exception as e:
        logger.error(f"Error listing subscriptions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list subscriptions: {str(e)}"
        )
