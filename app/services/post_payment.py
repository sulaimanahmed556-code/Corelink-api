"""
Post-Payment Account Creation

Called by webhook handlers (Stripe, Paystack, PayPal) after a successful
payment. Creates a group_admin account for the group owner so they can
log in to their own dashboard.
"""

import secrets
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group
from app.models.admin_account import AdminAccount, AdminRole
from app.services.admin_service import (
    create_admin_account,
    get_admin_by_email,
    hash_password,
)


async def create_group_admin_account(
    db: AsyncSession,
    group_id: str,
    subscriber_email: str,
) -> tuple[AdminAccount | None, str | None]:
    """
    Create (or return existing) group_admin account for a group owner.

    Called automatically after payment confirmation.

    Returns:
        (account, plain_password_or_None)
        plain_password is only returned on first creation so it can be
        sent to the user. On subsequent calls (e.g. webhook retry) returns
        (existing_account, None).
    """
    try:
        from uuid import UUID
        group_uuid = UUID(group_id)
    except ValueError:
        logger.error(f"Invalid group_id: {group_id}")
        return None, None

    # Check if this group already has an admin account
    result = await db.execute(
        select(AdminAccount).where(
            AdminAccount.group_id == group_uuid,
            AdminAccount.role == AdminRole.GROUP_ADMIN,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.info(f"Group admin account already exists for group {group_id}")
        return existing, None

    # Check if an account with this email already exists
    existing_email = await get_admin_by_email(db, subscriber_email)
    if existing_email:
        # Attach to group if not already attached
        if existing_email.group_id is None:
            existing_email.group_id = group_uuid
            await db.commit()
        return existing_email, None

    # Create new account
    plain_password = secrets.token_urlsafe(12)
    account = await create_admin_account(
        db=db,
        email=subscriber_email,
        password=plain_password,
        full_name=None,
        role=AdminRole.GROUP_ADMIN,
        group_id=group_uuid,
    )

    logger.success(
        f"Group admin account created for {subscriber_email} (group {group_id})"
    )
    return account, plain_password


async def provision_group_after_payment(
    db: AsyncSession,
    group_id: str,
    subscriber_email: str,
    plan_id: str | None = None,
) -> dict:
    """
    Full post-payment provisioning:
    1. Mark group as active + paid
    2. Create group_admin account
    3. Return credentials to send to the subscriber

    Returns a dict with account info and credentials.
    """
    try:
        from uuid import UUID
        group_uuid = UUID(group_id)
    except ValueError:
        return {"error": "invalid_group_id"}

    # Activate group
    result = await db.execute(select(Group).where(Group.id == group_uuid))
    group = result.scalar_one_or_none()
    if group:
        group.is_active = True
        group.has_made_payment = True
        await db.commit()
        logger.info(f"Group {group_id} activated after payment")

    # Create dashboard account
    account, plain_password = await create_group_admin_account(
        db=db,
        group_id=group_id,
        subscriber_email=subscriber_email,
    )

    if not account:
        return {"error": "account_creation_failed"}

    return {
        "account_id": str(account.id),
        "email": account.email,
        "role": account.role.value,
        "group_id": str(account.group_id) if account.group_id else None,
        "plain_password": plain_password,  # None if account pre-existed
        "is_new_account": plain_password is not None,
    }
