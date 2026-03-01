"""
CORELINK Task Queue Utilities

Functions for enqueuing and managing background tasks via Redis.
"""

import asyncio
import json
import uuid
from typing import Callable, Any, Coroutine
from datetime import datetime
from loguru import logger

from app.dependencies import get_redis_client


async def enqueue_task(
    task_func: Callable[..., Coroutine[Any, Any, Any]] | Callable[..., Any],
    *args: Any,
    task_name: str | None = None,
    priority: str = "normal",
    **kwargs: Any
) -> str:
    """
    Enqueue a background task to Redis queue for asynchronous execution.
    
    Supports both async and sync functions. Tasks are serialized and pushed
    to Redis with metadata for processing by workers.
    
    Args:
        task_func: Function to execute (async or sync)
        *args: Positional arguments for the function
        task_name: Optional human-readable task name
        priority: Task priority ('high', 'normal', 'low')
        **kwargs: Keyword arguments for the function
        
    Returns:
        Task ID (UUID string)
        
    Raises:
        Exception: If task enqueueing fails
        
    Usage:
        # From API route
        @router.post("/analyze")
        async def analyze_message(message_id: str):
            task_id = await enqueue_task(
                analyze_sentiment,
                message_id=message_id,
                task_name="Sentiment Analysis"
            )
            return {"task_id": task_id}
        
        # From bot handler
        async def handle_message(message: Message):
            await enqueue_task(
                process_message,
                message.text,
                user_id=message.from_user.id,
                task_name="Process Message"
            )
        
        # From scheduler
        async def daily_job():
            await enqueue_task(
                aggregate_analytics,
                days=1,
                priority="high"
            )
    """
    task_id = str(uuid.uuid4())
    task_name = task_name or task_func.__name__
    
    try:
        redis = await get_redis_client()
        
        # Serialize task data
        task_data = {
            "task_id": task_id,
            "task_name": task_name,
            "function": f"{task_func.__module__}.{task_func.__name__}",
            "args": args,
            "kwargs": kwargs,
            "is_async": asyncio.iscoroutinefunction(task_func),
            "priority": priority,
            "enqueued_at": datetime.utcnow().isoformat(),
            "status": "pending"
        }
        
        # Select queue based on priority
        queue_name = f"queue:{priority}"
        
        # Push to Redis list (LPUSH for FIFO with RPOP)
        await redis.lpush(queue_name, json.dumps(task_data))
        
        # Store task metadata in hash for status tracking
        await redis.hset(
            f"task:{task_id}",
            mapping={
                "name": task_name,
                "status": "pending",
                "enqueued_at": task_data["enqueued_at"],
                "priority": priority
            }
        )
        
        # Set expiration (24 hours)
        await redis.expire(f"task:{task_id}", 86400)
        
        logger.info(
            f"Task enqueued: {task_name} (ID: {task_id}, Priority: {priority})"
        )
        
        return task_id
        
    except Exception as e:
        logger.error(f"Failed to enqueue task '{task_name}': {str(e)}")
        raise


async def get_task_status(task_id: str) -> dict[str, Any] | None:
    """
    Get the current status of a queued task.
    
    Args:
        task_id: Task UUID
        
    Returns:
        Dictionary with task status or None if not found
        
    Usage:
        status = await get_task_status("task-uuid-here")
        if status:
            print(f"Status: {status['status']}")
    """
    try:
        redis = await get_redis_client()
        task_data = await redis.hgetall(f"task:{task_id}")
        
        if not task_data:
            return None
        
        return {
            "task_id": task_id,
            "name": task_data.get("name"),
            "status": task_data.get("status"),
            "enqueued_at": task_data.get("enqueued_at"),
            "started_at": task_data.get("started_at"),
            "completed_at": task_data.get("completed_at"),
            "priority": task_data.get("priority"),
            "error": task_data.get("error")
        }
        
    except Exception as e:
        logger.error(f"Failed to get task status for {task_id}: {str(e)}")
        return None


async def update_task_status(
    task_id: str,
    status: str,
    error: str | None = None
) -> bool:
    """
    Update task status in Redis.
    
    Args:
        task_id: Task UUID
        status: New status ('pending', 'running', 'completed', 'failed')
        error: Optional error message if failed
        
    Returns:
        True if updated successfully, False otherwise
        
    Usage:
        await update_task_status(task_id, "running")
        await update_task_status(task_id, "completed")
        await update_task_status(task_id, "failed", error="Connection timeout")
    """
    try:
        redis = await get_redis_client()
        
        updates = {"status": status}
        
        if status == "running":
            updates["started_at"] = datetime.utcnow().isoformat()
        elif status in ("completed", "failed"):
            updates["completed_at"] = datetime.utcnow().isoformat()
        
        if error:
            updates["error"] = error
        
        await redis.hset(f"task:{task_id}", mapping=updates)
        
        logger.info(f"Task {task_id} status updated to: {status}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update task status: {str(e)}")
        return False


async def get_queue_size(priority: str = "normal") -> int:
    """
    Get the number of pending tasks in a queue.
    
    Args:
        priority: Queue priority ('high', 'normal', 'low')
        
    Returns:
        Number of tasks in queue
        
    Usage:
        size = await get_queue_size("high")
        print(f"High priority queue has {size} tasks")
    """
    try:
        redis = await get_redis_client()
        size = await redis.llen(f"queue:{priority}")
        return size
        
    except Exception as e:
        logger.error(f"Failed to get queue size: {str(e)}")
        return 0


async def clear_queue(priority: str = "normal") -> int:
    """
    Clear all tasks from a queue.
    
    Args:
        priority: Queue priority to clear
        
    Returns:
        Number of tasks cleared
        
    Usage:
        cleared = await clear_queue("low")
        print(f"Cleared {cleared} tasks from low priority queue")
    """
    try:
        redis = await get_redis_client()
        size = await redis.llen(f"queue:{priority}")
        await redis.delete(f"queue:{priority}")
        
        logger.warning(f"Cleared {size} tasks from {priority} priority queue")
        return size
        
    except Exception as e:
        logger.error(f"Failed to clear queue: {str(e)}")
        return 0


async def enqueue_batch(
    tasks: list[tuple[Callable, tuple, dict]],
    priority: str = "normal"
) -> list[str]:
    """
    Enqueue multiple tasks in a batch operation.
    
    Args:
        tasks: List of (function, args, kwargs) tuples
        priority: Priority for all tasks
        
    Returns:
        List of task IDs
        
    Usage:
        tasks = [
            (analyze_sentiment, ("text1",), {}),
            (analyze_sentiment, ("text2",), {}),
            (analyze_sentiment, ("text3",), {})
        ]
        task_ids = await enqueue_batch(tasks, priority="high")
    """
    task_ids = []
    
    for task_func, args, kwargs in tasks:
        try:
            task_id = await enqueue_task(
                task_func,
                *args,
                priority=priority,
                **kwargs
            )
            task_ids.append(task_id)
        except Exception as e:
            logger.error(f"Failed to enqueue task in batch: {str(e)}")
    
    logger.info(f"Batch enqueued {len(task_ids)}/{len(tasks)} tasks")
    return task_ids


async def cancel_task(task_id: str) -> bool:
    """
    Mark a task as cancelled (if not already running).
    
    Args:
        task_id: Task UUID to cancel
        
    Returns:
        True if cancelled, False otherwise
        
    Usage:
        success = await cancel_task("task-uuid-here")
    """
    try:
        redis = await get_redis_client()
        
        # Check current status
        status = await redis.hget(f"task:{task_id}", "status")
        
        if status == "running":
            logger.warning(f"Cannot cancel running task: {task_id}")
            return False
        
        if status in ("completed", "failed", "cancelled"):
            logger.info(f"Task already in terminal state: {task_id}")
            return False
        
        # Mark as cancelled
        await update_task_status(task_id, "cancelled")
        logger.info(f"Task cancelled: {task_id}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {str(e)}")
        return False
