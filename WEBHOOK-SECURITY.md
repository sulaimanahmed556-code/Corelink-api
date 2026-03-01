# CORELINK Webhook Security

## 🔒 Overview

CORELINK implements comprehensive webhook security to protect against unauthorized access, replay attacks, and abuse. All webhook endpoints are validated before processing.

---

## ✅ Security Features

### 1. **Secret Token Validation**
- All webhook requests must include valid secret headers
- Secrets are compared in constant-time to prevent timing attacks
- Invalid requests are rejected with 401 Unauthorized

### 2. **HTTPS Enforcement**
- Production mode requires HTTPS for all webhook requests
- Checks `X-Forwarded-Proto` header (set by nginx)
- Insecure requests are rejected with 403 Forbidden

### 3. **Failed Attempt Tracking**
- Tracks failed authentication attempts per IP address
- Temporary IP blocking after 10 failed attempts
- 15-minute block duration (configurable)

### 4. **Suspicious Request Logging**
- All failed webhook attempts are logged with details:
  - IP address
  - Request path
  - User-Agent
  - Headers
  - Timestamp

### 5. **Rate Limiting**
- Additional rate limiting at nginx level
- Per-IP request limits
- Burst allowance for legitimate traffic spikes

---

## 🔐 Webhook Endpoints & Validation

### Telegram Webhook
**Endpoint:** `POST /api/v1/webhook/telegram`

**Validation:**
- Header: `X-Telegram-Bot-Api-Secret-Token`
- Secret: `TELEGRAM_WEBHOOK_SECRET` from config
- Set during webhook registration with Telegram

**Example:**
```bash
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://yourdomain.com/api/v1/webhook/telegram",
    "secret_token": "your_telegram_webhook_secret"
  }'
```

### Stripe Webhook
**Endpoint:** `POST /api/v1/payments/stripe/webhook`

**Validation:**
- Header: `Stripe-Signature`
- Verified using `stripe.Webhook.construct_event()`
- Secret: `STRIPE_WEBHOOK_SECRET` from config

**Example:**
```python
import stripe

# Stripe automatically sends Stripe-Signature header
# Verification happens in route handler:
event = stripe.Webhook.construct_event(
    payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
)
```

### Paystack Webhook
**Endpoint:** `POST /api/v1/payments/paystack/webhook`

**Validation:**
- Header: `X-Paystack-Signature`
- HMAC SHA-512 signature verification
- Secret: `PAYSTACK_SECRET_KEY` from config

**Example:**
```python
import hmac
import hashlib

# Verify signature
computed_signature = hmac.new(
    settings.PAYSTACK_SECRET_KEY.encode(),
    payload,
    hashlib.sha512
).hexdigest()

is_valid = hmac.compare_digest(computed_signature, received_signature)
```

### PayPal Webhook
**Endpoint:** `POST /api/v1/payments/paypal/webhook`

**Validation:**
- OAuth 2.0 token verification
- Webhook event verification via PayPal API
- Uses `PAYPAL_CLIENT_ID` and `PAYPAL_SECRET`

---

## 🛡️ Middleware Configuration

### WebhookSecurityMiddleware

Located in: `backend/app/middleware/webhook_security.py`

**Features:**
- Automatic webhook endpoint detection
- Secret validation based on endpoint type
- IP-based failed attempt tracking
- HTTPS enforcement in production
- Detailed logging of suspicious requests

**Configuration:**
```python
# In backend/app/middleware/webhook_security.py

# Maximum failed attempts before temporary block
MAX_FAILED_ATTEMPTS = 10

# Block duration in minutes
BLOCK_DURATION_MINUTES = 15
```

**Customization:**
```python
# Adjust limits
MAX_FAILED_ATTEMPTS = 20  # More lenient
BLOCK_DURATION_MINUTES = 30  # Longer block

# Or more strict
MAX_FAILED_ATTEMPTS = 5  # Stricter
BLOCK_DURATION_MINUTES = 60  # 1-hour block
```

---

## 📊 Security Monitoring

### View Security Stats

**Endpoint:** `GET /security/webhook-stats`

**Response:**
```json
{
  "status": "ok",
  "webhook_security": {
    "blocked_ips": 2,
    "ips_with_failures": 5,
    "total_tracked_ips": 10,
    "max_failed_attempts": 10,
    "block_duration_minutes": 15
  },
  "environment": "production",
  "https_enforced": true
}
```

**Usage:**
```bash
# Check security stats
curl https://yourdomain.com/security/webhook-stats

# In production, protect this endpoint with authentication
```

### Log Files

**Location:** `backend/logs/corelink_YYYY-MM-DD.log`

**Log Rotation:**
- Daily rotation at midnight
- 30-day retention
- Automatic compression (zip)

**Example Log Entry:**
```
2026-01-15 10:30:45 | WARNING  | app.middleware.webhook_security:_log_suspicious_request:234 - Suspicious webhook request detected:
  IP: 192.168.1.100
  Path: /api/v1/webhook/telegram
  Method: POST
  User-Agent: curl/7.68.0
  Referer: none
  X-Forwarded-For: none
  Timestamp: 2026-01-15T10:30:45.123456
```

---

## 🔧 Configuration

### Environment Variables

```bash
# Telegram webhook secret
TELEGRAM_WEBHOOK_SECRET=your_random_secret_here

# Stripe webhook secret
STRIPE_WEBHOOK_SECRET=whsec_your_stripe_secret

# Paystack secret key (used for signature verification)
PAYSTACK_SECRET_KEY=sk_your_paystack_secret

# PayPal credentials
PAYPAL_CLIENT_ID=your_paypal_client_id
PAYPAL_SECRET=your_paypal_secret
PAYPAL_WEBHOOK_ID=your_paypal_webhook_id

# Environment (affects HTTPS enforcement)
ENV=production  # or development
```

### Generate Secure Secrets

```bash
# Generate random secrets
openssl rand -hex 32  # For TELEGRAM_WEBHOOK_SECRET
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Or use Python
import secrets
telegram_secret = secrets.token_urlsafe(32)
print(f"TELEGRAM_WEBHOOK_SECRET={telegram_secret}")
```

---

## 🚀 Deployment

### 1. Set Environment Variables

```bash
# Edit .env file
nano .env

# Add webhook secrets
TELEGRAM_WEBHOOK_SECRET=your_generated_secret
STRIPE_WEBHOOK_SECRET=whsec_from_stripe_dashboard
PAYSTACK_SECRET_KEY=sk_from_paystack_dashboard
```

### 2. Configure Telegram Webhook

```bash
# Set webhook with secret
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://yourdomain.com/api/v1/webhook/telegram",
    "secret_token": "your_telegram_webhook_secret",
    "max_connections": 40,
    "allowed_updates": ["message", "callback_query"]
  }'

# Verify webhook
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

### 3. Configure Payment Webhooks

**Stripe:**
1. Go to Stripe Dashboard → Developers → Webhooks
2. Add endpoint: `https://yourdomain.com/api/v1/payments/stripe/webhook`
3. Select events to listen for
4. Copy webhook signing secret to `.env`

**Paystack:**
1. Go to Paystack Dashboard → Settings → Webhooks
2. Add endpoint: `https://yourdomain.com/api/v1/payments/paystack/webhook`
3. Secret key is your Paystack secret key

**PayPal:**
1. Go to PayPal Developer Dashboard → Webhooks
2. Add endpoint: `https://yourdomain.com/api/v1/payments/paypal/webhook`
3. Select event types
4. Copy webhook ID to `.env`

### 4. Test Webhook Security

```bash
# Test with valid secret
curl -X POST "https://yourdomain.com/api/v1/webhook/telegram" \
  -H "X-Telegram-Bot-Api-Secret-Token: your_telegram_webhook_secret" \
  -H "Content-Type: application/json" \
  -d '{"update_id": 123}'

# Expected: 200 OK

# Test with invalid secret
curl -X POST "https://yourdomain.com/api/v1/webhook/telegram" \
  -H "X-Telegram-Bot-Api-Secret-Token: wrong_secret" \
  -H "Content-Type: application/json" \
  -d '{"update_id": 123}'

# Expected: 401 Unauthorized

# Test without secret
curl -X POST "https://yourdomain.com/api/v1/webhook/telegram" \
  -H "Content-Type: application/json" \
  -d '{"update_id": 123}'

# Expected: 401 Unauthorized
```

---

## 🔍 Troubleshooting

### Issue: Webhook Returns 401 Unauthorized

**Cause:** Invalid or missing secret token

**Solution:**
```bash
# Check secret in .env
grep TELEGRAM_WEBHOOK_SECRET .env

# Verify webhook is set with same secret
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"

# Re-set webhook with correct secret
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -d "url=https://yourdomain.com/api/v1/webhook/telegram" \
  -d "secret_token=your_telegram_webhook_secret"
```

### Issue: Webhook Returns 403 Forbidden

**Cause:** HTTPS enforcement in production

**Solution:**
```bash
# Verify HTTPS is working
curl -I https://yourdomain.com/health

# Check X-Forwarded-Proto header
curl -I https://yourdomain.com/health | grep -i forwarded

# Verify nginx is setting headers correctly
docker-compose logs nginx | grep -i forwarded
```

### Issue: IP Temporarily Blocked

**Cause:** Too many failed attempts

**Solution:**
```bash
# Check security stats
curl https://yourdomain.com/security/webhook-stats

# View logs
docker-compose logs backend | grep "temporarily blocking"

# Wait 15 minutes or restart service to clear blocks
docker-compose restart backend
```

### Issue: Logs Not Being Created

**Cause:** Logs directory doesn't exist or no write permissions

**Solution:**
```bash
# Create logs directory
mkdir -p backend/logs

# Set permissions
chmod 755 backend/logs

# Verify logging in container
docker-compose exec backend ls -la /app/logs/
```

---

## 📈 Performance Impact

### Minimal Overhead:
- **Secret Validation:** ~0.1ms per request
- **IP Tracking:** ~0.05ms per request
- **Logging:** Async, non-blocking
- **Total Impact:** < 1% CPU increase

### Memory Usage:
- **Failed Attempts Tracking:** ~1KB per IP
- **Max Memory:** ~10MB for 10,000 tracked IPs
- **Auto-Cleanup:** Old attempts removed automatically

---

## 🔐 Best Practices

### 1. Use Strong Secrets
```bash
# Generate cryptographically secure secrets
openssl rand -hex 32  # 64 characters
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2. Rotate Secrets Regularly
```bash
# Every 90 days, generate new secrets
# Update .env
# Re-configure webhooks with new secrets
```

### 3. Monitor Security Logs
```bash
# Set up log monitoring
tail -f backend/logs/corelink_$(date +%Y-%m-%d).log | grep WARNING

# Or use log aggregation service (e.g., ELK, Splunk)
```

### 4. Protect Admin Endpoints
```python
# Add authentication to security stats endpoint
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer

security = HTTPBearer()

@app.get("/security/webhook-stats")
async def webhook_security_stats(token: str = Depends(security)):
    # Verify admin token
    if token.credentials != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    # ... return stats
```

### 5. Use HTTPS Everywhere
```bash
# Ensure all webhooks use HTTPS
# Never use HTTP in production
# Verify SSL certificate is valid
```

---

## 📚 Additional Resources

- [Telegram Bot API - Webhooks](https://core.telegram.org/bots/api#setwebhook)
- [Stripe Webhook Security](https://stripe.com/docs/webhooks/signatures)
- [Paystack Webhook Security](https://paystack.com/docs/payments/webhooks/)
- [PayPal Webhook Security](https://developer.paypal.com/docs/api-basics/notifications/webhooks/notification-messages/)
- [OWASP Webhook Security](https://cheatsheetseries.owasp.org/cheatsheets/Webhook_Security_Cheat_Sheet.html)

---

## 🆘 Support

For security issues or questions:
1. Check logs: `docker-compose logs backend | grep -i security`
2. Review security stats: `curl /security/webhook-stats`
3. Consult troubleshooting section above

For security vulnerabilities, see [SECURITY.md](../SECURITY.md) for responsible disclosure.

---

**Last Updated:** January 2026  
**Version:** 1.0.0  
**Security Level:** Production-Ready ✅
