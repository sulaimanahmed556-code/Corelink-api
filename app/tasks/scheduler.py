"""
CORELINK Task Scheduler

Background task scheduling using APScheduler with asyncio support.
Handles recurring tasks like daily aggregations and weekly reports.
"""

import asyncio
from typing import Callable, Any
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from app.config import settings


# Global scheduler instance
scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """
    Get or create the global scheduler instance.
    
    Returns:
        AsyncIOScheduler instance
    """
    global scheduler
    if scheduler is None:
        scheduler = AsyncIOScheduler(
            timezone="UTC",
            job_defaults={
                'coalesce': True,  # Combine multiple missed runs into one
                'max_instances': 1,  # Prevent concurrent runs of same job
                'misfire_grace_time': 300  # Allow 5 minutes grace for missed jobs
            }
        )
    return scheduler


async def enqueue_task(
    func: Callable,
    *args: Any,
    task_name: str | None = None,
    **kwargs: Any
) -> None:
    """
    Enqueue a background task for immediate execution.
    
    Args:
        func: Async function to execute
        *args: Positional arguments for the function
        task_name: Optional name for logging
        **kwargs: Keyword arguments for the function
        
    Usage:
        await enqueue_task(process_message, message_id=123, task_name="Process Message")
    """
    task_name = task_name or func.__name__
    
    try:
        logger.info(f"Enqueuing task: {task_name}")
        
        # Execute async function
        if asyncio.iscoroutinefunction(func):
            await func(*args, **kwargs)
        else:
            # Run sync function in executor
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, func, *args, **kwargs)
        
        logger.success(f"Task completed: {task_name}")
        
    except Exception as e:
        logger.error(f"Task failed: {task_name} - {str(e)}")
        raise


def schedule_recurring_tasks() -> None:
    """
    Schedule all recurring background tasks.
    
    Scheduled tasks:
    - daily_aggregation: Runs daily at 1 AM UTC
    - weekly_reports: Runs weekly on Monday at 9 AM UTC (configurable)
    """
    sched = get_scheduler()
    
    # Import task functions
    from app.tasks.weekly_reports import (
        run_daily_aggregation,
        run_weekly_reports
    )
    
    # Schedule daily aggregation (every day at 1:00 AM UTC)
    sched.add_job(
        run_daily_aggregation,
        trigger=CronTrigger(hour=1, minute=0),
        id="daily_aggregation",
        name="Daily Analytics Aggregation",
        replace_existing=True
    )
    logger.info("Scheduled task: daily_aggregation (1:00 AM UTC daily)")
    
    # Schedule weekly reports (configurable via settings)
    # Default: Monday at 9:00 AM UTC
    sched.add_job(
        run_weekly_reports,
        trigger=CronTrigger.from_crontab(settings.WEEKLY_REPORT_CRON),
        id="weekly_reports",
        name="Weekly Group Reports",
        replace_existing=True
    )
    logger.info(
        f"Scheduled task: weekly_reports ({settings.WEEKLY_REPORT_CRON})"
    )


async def start_scheduler() -> None:
    """
    Start the background task scheduler.
    
    Call this function in FastAPI startup event.
    
    Usage:
        @app.on_event("startup")
        async def startup():
            await start_scheduler()
    """
    if not settings.ENABLE_SCHEDULER:
        logger.warning("Scheduler is disabled in configuration")
        return
    
    try:
        sched = get_scheduler()
        
        # Schedule recurring tasks
        schedule_recurring_tasks()
        
        # Start scheduler
        sched.start()
        
        logger.success("Task scheduler started successfully")
        logger.info(f"Scheduled jobs: {len(sched.get_jobs())}")
        
        # Log scheduled jobs
        for job in sched.get_jobs():
            logger.info(f"  - {job.name} (ID: {job.id})")
        
    except Exception as e:
        logger.error(f"Failed to start scheduler: {str(e)}")
        raise


async def stop_scheduler() -> None:
    """
    Stop the background task scheduler gracefully.
    
    Call this function in FastAPI shutdown event.
    
    Usage:
        @app.on_event("shutdown")
        async def shutdown():
            await stop_scheduler()
    """
    global scheduler
    
    if scheduler is None:
        return
    
    try:
        # Wait for running jobs to complete (max 30 seconds)
        scheduler.shutdown(wait=True)
        logger.info("Task scheduler stopped successfully")
        
    except Exception as e:
        logger.error(f"Error stopping scheduler: {str(e)}")
    
    finally:
        scheduler = None


def list_scheduled_jobs() -> list[dict[str, Any]]:
    """
    Get list of all scheduled jobs.
    
    Returns:
        List of job information dictionaries
        
    Usage:
        jobs = list_scheduled_jobs()
        for job in jobs:
            print(f"{job['name']}: {job['next_run']}")
    """
    sched = get_scheduler()
    
    jobs = []
    for job in sched.get_jobs():
        jobs.append({
            'id': job.id,
            'name': job.name,
            'next_run': job.next_run_time,
            'trigger': str(job.trigger)
        })
    
    return jobs


async def run_task_now(task_id: str) -> bool:
    """
    Manually trigger a scheduled task to run immediately.
    
    Args:
        task_id: ID of the task to run
        
    Returns:
        True if task was triggered, False if not found
        
    Usage:
        success = await run_task_now("weekly_reports")
    """
    sched = get_scheduler()
    
    job = sched.get_job(task_id)
    if job is None:
        logger.warning(f"Task not found: {task_id}")
        return False
    
    try:
        logger.info(f"Manually triggering task: {job.name}")
        job.modify(next_run_time=datetime.utcnow())
        return True
        
    except Exception as e:
        logger.error(f"Failed to trigger task {task_id}: {str(e)}")
        return False
