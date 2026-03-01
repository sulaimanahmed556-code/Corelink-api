"""
CORELINK Plans Routes

Admin endpoints for managing subscription plans.
Plans are automatically created on Stripe, Paystack, and PayPal
when created here.
"""

from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_super_admin
from app.database import get_db
from app.models import Plan
from app.services.plan_provisioner import provision_plan_on_all_providers

router = APIRouter(dependencies=[Depends(require_super_admin)])


# ---------------------------------------------------------------------------
# Available feature keys
# ---------------------------------------------------------------------------

AVAILABLE_FEATURES = [
    {"key": "churn_detection", "label": "Churn Detection", "description": "Auto-remove inactive or rule-breaking users"},
    {"key": "sentiment_analysis", "label": "Sentiment Analysis", "description": "Analyse emotional tone of messages"},
    {"key": "weekly_reports", "label": "Weekly Reports", "description": "Automated weekly digest sent to group admins"},
    {"key": "message_summarization", "label": "Message Summarization", "description": "AI-powered group conversation summaries"},
    {"key": "user_analytics", "label": "User Analytics", "description": "Deep analytics on user activity and engagement"},
    {"key": "rule_enforcement", "label": "Rule Enforcement", "description": "Automated rule-based moderation actions"},
    {"key": "group_management", "label": "Group Management", "description": "Full control over group settings via bot"},
]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreatePlanRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    price: Decimal = Field(..., gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=10)
    interval: str = Field(default="month", description="month or year")
    interval_count: int = Field(default=1, ge=1)
    features: list[str] = Field(default_factory=list, description="List of feature keys")
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        return v.strip()

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("interval")
    @classmethod
    def normalize_interval(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in ("month", "year", "week", "day"):
            raise ValueError("interval must be one of: month, year, week, day")
        return v

    @field_validator("features")
    @classmethod
    def validate_features(cls, v: list[str]) -> list[str]:
        valid = {f["key"] for f in AVAILABLE_FEATURES}
        for key in v:
            if key not in valid:
                raise ValueError(f"Unknown feature key: {key}")
        return list(set(v))  # deduplicate


class UpdatePlanRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    price: Optional[Decimal] = Field(None, gt=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=10)
    features: Optional[list[str]] = None
    is_active: Optional[bool] = None

    @field_validator("features")
    @classmethod
    def validate_features(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        valid = {f["key"] for f in AVAILABLE_FEATURES}
        for key in v:
            if key not in valid:
                raise ValueError(f"Unknown feature key: {key}")
        return list(set(v))


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: Optional[str] = None
    price: Decimal
    currency: str
    interval: str
    interval_count: int
    features: list[str]
    stripe_plan_id: Optional[str] = None
    paypal_plan_id: Optional[str] = None
    paystack_plan_code: Optional[str] = None
    is_active: bool
    created_at: str
    updated_at: str
    provider_status: dict


def _plan_to_response(plan: Plan) -> PlanResponse:
    return PlanResponse(
        id=plan.id,
        name=plan.name,
        description=plan.description,
        price=plan.price,
        currency=plan.currency,
        interval=plan.interval,
        interval_count=plan.interval_count,
        features=plan.features or [],
        stripe_plan_id=plan.stripe_plan_id,
        paypal_plan_id=plan.paypal_plan_id,
        paystack_plan_code=plan.paystack_plan_code,
        is_active=plan.is_active,
        created_at=plan.created_at.isoformat(),
        updated_at=plan.updated_at.isoformat(),
        provider_status={
            "stripe": bool(plan.stripe_plan_id),
            "paystack": bool(plan.paystack_plan_code),
            "paypal": bool(plan.paypal_plan_id),
        },
    )


def _parse_uuid(value: str, field_name: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}: must be a valid UUID",
        ) from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/features")
async def list_features() -> JSONResponse:
    """Return all available feature keys that can be assigned to a plan."""
    return JSONResponse(content={"features": AVAILABLE_FEATURES})


@router.post("/", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    req: CreatePlanRequest,
    db: AsyncSession = Depends(get_db),
) -> PlanResponse:
    """
    Create a billing plan and auto-provision it on Stripe, Paystack, and PayPal.
    """
    try:
        # Check name uniqueness
        existing = await db.execute(select(Plan).where(Plan.name == req.name))
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A plan with this name already exists",
            )

        # Auto-create on all providers
        logger.info(f"Provisioning plan '{req.name}' on all payment providers…")
        provider_ids = await provision_plan_on_all_providers(
            name=req.name,
            description=req.description,
            price=req.price,
            currency=req.currency,
            interval=req.interval,
            interval_count=req.interval_count,
        )

        plan = Plan(
            name=req.name,
            description=req.description,
            price=req.price,
            currency=req.currency,
            interval=req.interval,
            interval_count=req.interval_count,
            features=req.features,
            is_active=req.is_active,
            stripe_plan_id=provider_ids.get("stripe_plan_id"),
            paypal_plan_id=provider_ids.get("paypal_plan_id"),
            paystack_plan_code=provider_ids.get("paystack_plan_code"),
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)

        logger.success(f"Plan '{req.name}' created (id={plan.id})")
        return _plan_to_response(plan)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error creating plan: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create plan",
        ) from exc


@router.get("/", response_model=dict)
async def list_plans(
    active_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List all plans."""
    try:
        query = select(Plan)
        if active_only:
            query = query.where(Plan.is_active.is_(True))
        query = query.order_by(Plan.price.asc(), Plan.created_at.desc()).limit(limit).offset(offset)

        result = await db.execute(query)
        plans = result.scalars().all()

        return {
            "total": len(plans),
            "plans": [_plan_to_response(p).model_dump() for p in plans],
        }
    except Exception as exc:
        logger.error(f"Error listing plans: {exc}")
        raise HTTPException(status_code=500, detail="Failed to list plans")


@router.get("/{plan_id}", response_model=PlanResponse)
async def get_plan(plan_id: str, db: AsyncSession = Depends(get_db)) -> PlanResponse:
    uuid = _parse_uuid(plan_id, "plan_id")
    result = await db.execute(select(Plan).where(Plan.id == uuid))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return _plan_to_response(plan)


@router.put("/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: str,
    req: UpdatePlanRequest,
    db: AsyncSession = Depends(get_db),
) -> PlanResponse:
    """Update plan metadata. Provider IDs are not changed here."""
    try:
        uuid = _parse_uuid(plan_id, "plan_id")
        result = await db.execute(select(Plan).where(Plan.id == uuid))
        plan = result.scalar_one_or_none()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")

        updates = req.model_dump(exclude_unset=True)
        if "name" in updates and updates["name"] != plan.name:
            dup = await db.execute(select(Plan).where(Plan.name == updates["name"]))
            if dup.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="A plan with this name already exists")

        for key, value in updates.items():
            setattr(plan, key, value)

        await db.commit()
        await db.refresh(plan)
        return _plan_to_response(plan)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error updating plan {plan_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to update plan")


@router.delete("/{plan_id}")
async def deactivate_plan(plan_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Deactivate a plan (soft delete)."""
    uuid = _parse_uuid(plan_id, "plan_id")
    result = await db.execute(select(Plan).where(Plan.id == uuid))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    plan.is_active = False
    await db.commit()
    return JSONResponse(content={"status": "deactivated", "plan_id": str(plan.id)})
