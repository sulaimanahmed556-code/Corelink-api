"""
CORELINK Webhook Security Middleware

Validates webhook requests and logs suspicious activity.
"""

import time
from typing import Callable
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


# Track failed attempts per IP for rate limiting
failed_attempts: dict[str, list[datetime]] = defaultdict(list)

# Maximum failed attempts before temporary block
MAX_FAILED_ATTEMPTS = 10
BLOCK_DURATION_MINUTES = 15


class WebhookSecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce security on webhook endpoints.
    
    Features:
    - Validates webhook secret headers
    - Logs suspicious requests
    - Tracks failed authentication attempts
    - Temporary IP blocking after repeated failures
    - HTTPS enforcement (when behind reverse proxy)
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request and enforce webhook security.
        
        Args:
            request: Incoming HTTP request
            call_next: Next middleware or route handler
            
        Returns:
            Response from handler or security rejection
        """
        # Only apply to webhook endpoints
        if not self._is_webhook_endpoint(request.url.path):
            return await call_next(request)
        
        # Get client IP (considering reverse proxy)
        client_ip = self._get_client_ip(request)
        
        # Check if IP is temporarily blocked
        if self._is_ip_blocked(client_ip):
            logger.warning(
                f"Blocked webhook request from temporarily banned IP: {client_ip}, "
                f"path: {request.url.path}"
            )
            return JSONResponse(
                content={"error": "Too many failed attempts. Try again later."},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS
            )
        
        # Enforce HTTPS in production (when behind reverse proxy)
        if not self._is_secure_connection(request):
            logger.warning(
                f"Insecure webhook request rejected: {client_ip}, "
                f"path: {request.url.path}, "
                f"proto: {request.headers.get('X-Forwarded-Proto', 'unknown')}"
            )
            return JSONResponse(
                content={"error": "HTTPS required"},
                status_code=status.HTTP_403_FORBIDDEN
            )
        
        # Validate webhook secret based on endpoint
        is_valid = await self._validate_webhook_secret(request)
        
        if not is_valid:
            # Log suspicious request
            self._log_suspicious_request(request, client_ip)
            
            # Track failed attempt
            self._record_failed_attempt(client_ip)
            
            # Return unauthorized response
            return JSONResponse(
                content={"error": "Invalid or missing webhook secret"},
                status_code=status.HTTP_401_UNAUTHORIZED
            )
        
        # Valid request - proceed
        logger.debug(
            f"Valid webhook request: {client_ip}, "
            f"path: {request.url.path}, "
            f"method: {request.method}"
        )
        
        # Clear failed attempts on successful auth
        if client_ip in failed_attempts:
            failed_attempts[client_ip].clear()
        
        return await call_next(request)
    
    def _is_webhook_endpoint(self, path: str) -> bool:
        """
        Check if path is a webhook endpoint.
        
        Args:
            path: Request URL path
            
        Returns:
            True if webhook endpoint, False otherwise
        """
        webhook_patterns = [
            "/webhook/telegram",
            "/webhook/stripe",
            "/webhook/paystack",
            "/webhook/paypal",
            "/payments/stripe/webhook",
            "/payments/paystack/webhook",
            "/payments/paypal/webhook",
        ]
        
        return any(pattern in path for pattern in webhook_patterns)
    
    def _get_client_ip(self, request: Request) -> str:
        """
        Get client IP address, considering reverse proxy headers.
        
        Args:
            request: HTTP request
            
        Returns:
            Client IP address
        """
        # Check X-Forwarded-For header (set by nginx)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take first IP in chain (original client)
            return forwarded_for.split(",")[0].strip()
        
        # Check X-Real-IP header (alternative)
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        
        # Fallback to direct connection IP
        if request.client:
            return request.client.host
        
        return "unknown"
    
    def _is_secure_connection(self, request: Request) -> bool:
        """
        Check if connection is secure (HTTPS).
        
        Args:
            request: HTTP request
            
        Returns:
            True if HTTPS or development mode, False otherwise
        """
        # Allow insecure in development
        if settings.is_development:
            return True
        
        # Check X-Forwarded-Proto header (set by nginx)
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "").lower()
        if forwarded_proto == "https":
            return True
        
        # Check direct HTTPS connection
        if request.url.scheme == "https":
            return True
        
        # In production, require HTTPS
        return False
    
    async def _validate_webhook_secret(self, request: Request) -> bool:
        """
        Validate webhook secret based on endpoint type.
        
        Args:
            request: HTTP request
            
        Returns:
            True if valid, False otherwise
        """
        path = request.url.path
        
        # Telegram webhook validation
        if "/webhook/telegram" in path:
            secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            return secret_header == settings.TELEGRAM_WEBHOOK_SECRET
        
        # Stripe webhook validation (signature-based, validated in route)
        if "stripe/webhook" in path:
            # Signature validation happens in route handler
            # Just check header exists
            return request.headers.get("Stripe-Signature") is not None
        
        # Paystack webhook validation
        if "paystack/webhook" in path:
            # Signature validation happens in route handler
            return request.headers.get("X-Paystack-Signature") is not None
        
        # PayPal webhook validation
        if "paypal/webhook" in path:
            # Signature validation happens in route handler
            return True  # PayPal uses OAuth, validated in route
        
        # Unknown webhook endpoint - deny by default
        logger.warning(f"Unknown webhook endpoint: {path}")
        return False
    
    def _log_suspicious_request(self, request: Request, client_ip: str) -> None:
        """
        Log suspicious webhook request for security monitoring.
        
        Args:
            request: HTTP request
            client_ip: Client IP address
        """
        logger.warning(
            f"Suspicious webhook request detected:\n"
            f"  IP: {client_ip}\n"
            f"  Path: {request.url.path}\n"
            f"  Method: {request.method}\n"
            f"  User-Agent: {request.headers.get('User-Agent', 'unknown')}\n"
            f"  Referer: {request.headers.get('Referer', 'none')}\n"
            f"  X-Forwarded-For: {request.headers.get('X-Forwarded-For', 'none')}\n"
            f"  Timestamp: {datetime.utcnow().isoformat()}"
        )
    
    def _record_failed_attempt(self, client_ip: str) -> None:
        """
        Record failed authentication attempt for IP.
        
        Args:
            client_ip: Client IP address
        """
        now = datetime.utcnow()
        
        # Remove old attempts (older than block duration)
        cutoff = now - timedelta(minutes=BLOCK_DURATION_MINUTES)
        failed_attempts[client_ip] = [
            attempt for attempt in failed_attempts[client_ip]
            if attempt > cutoff
        ]
        
        # Add new attempt
        failed_attempts[client_ip].append(now)
        
        # Log if approaching limit
        attempt_count = len(failed_attempts[client_ip])
        if attempt_count >= MAX_FAILED_ATTEMPTS:
            logger.error(
                f"IP {client_ip} has {attempt_count} failed webhook attempts. "
                f"Temporarily blocking for {BLOCK_DURATION_MINUTES} minutes."
            )
        elif attempt_count >= MAX_FAILED_ATTEMPTS // 2:
            logger.warning(
                f"IP {client_ip} has {attempt_count} failed webhook attempts. "
                f"Will be blocked at {MAX_FAILED_ATTEMPTS} attempts."
            )
    
    def _is_ip_blocked(self, client_ip: str) -> bool:
        """
        Check if IP is temporarily blocked due to failed attempts.
        
        Args:
            client_ip: Client IP address
            
        Returns:
            True if blocked, False otherwise
        """
        if client_ip not in failed_attempts:
            return False
        
        # Clean old attempts
        now = datetime.utcnow()
        cutoff = now - timedelta(minutes=BLOCK_DURATION_MINUTES)
        failed_attempts[client_ip] = [
            attempt for attempt in failed_attempts[client_ip]
            if attempt > cutoff
        ]
        
        # Check if over limit
        return len(failed_attempts[client_ip]) >= MAX_FAILED_ATTEMPTS


def get_webhook_stats() -> dict:
    """
    Get webhook security statistics.
    
    Returns:
        Dictionary with security stats
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=BLOCK_DURATION_MINUTES)
    
    # Count active blocks
    blocked_ips = [
        ip for ip, attempts in failed_attempts.items()
        if len([a for a in attempts if a > cutoff]) >= MAX_FAILED_ATTEMPTS
    ]
    
    # Count IPs with recent failures
    ips_with_failures = [
        ip for ip, attempts in failed_attempts.items()
        if len([a for a in attempts if a > cutoff]) > 0
    ]
    
    return {
        "blocked_ips": len(blocked_ips),
        "ips_with_failures": len(ips_with_failures),
        "total_tracked_ips": len(failed_attempts),
        "max_failed_attempts": MAX_FAILED_ATTEMPTS,
        "block_duration_minutes": BLOCK_DURATION_MINUTES,
    }
