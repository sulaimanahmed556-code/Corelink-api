"""
CORELINK Utility Modules

Exports utility functions for tasks, queues, and helpers.
"""

from app.utils.queue import (
    enqueue_task,
    get_task_status,
    update_task_status,
    get_queue_size,
    clear_queue,
    enqueue_batch,
    cancel_task,
)
from app.utils.nlp import (
    clean_text,
    preprocess_messages,
    remove_bot_messages,
    truncate_text,
    is_meaningful_text,
)
from app.utils.access import (
    check_group_access,
    check_group_access_with_db,
    get_subscription_status,
    require_active_subscription,
    get_access_denial_reason,
    get_groups_with_expiring_subscriptions,
    count_active_subscriptions_by_provider,
    require_subscription_dependency,
)

__all__ = [
    # Queue
    "enqueue_task",
    "get_task_status",
    "update_task_status",
    "get_queue_size",
    "clear_queue",
    "enqueue_batch",
    "cancel_task",
    
    # NLP
    "clean_text",
    "preprocess_messages",
    "remove_bot_messages",
    "truncate_text",
    "is_meaningful_text",
    
    # Access Control
    "check_group_access",
    "check_group_access_with_db",
    "get_subscription_status",
    "require_active_subscription",
    "get_access_denial_reason",
    "get_groups_with_expiring_subscriptions",
    "count_active_subscriptions_by_provider",
    "require_subscription_dependency",
]
