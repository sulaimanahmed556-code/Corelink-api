"""
CORELINK Configuration Module

Production-ready configuration using Pydantic Settings v2.
All sensitive data loaded from environment variables.
"""

from typing import Literal
from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings with type validation and environment variable loading.
    
    All settings are loaded from environment variables or .env file.
    No secrets are hardcoded.
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )
    
    # Application Settings
    APP_NAME: str = "CORELINK"
    ENV: Literal["development", "production"] = Field(
        default="development",
        description="Application environment"
    )
    DEBUG: bool = Field(
        default=False,
        description="Debug mode (should be False in production)"
    )
    
    # Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN: str = Field(
        ...,
        description="Telegram bot token from BotFather"
    )
    TELEGRAM_WEBHOOK_SECRET: str = Field(
        ...,
        description="Secret token for webhook validation"
    )
    TELEGRAM_WEBHOOK_URL: str = Field(
        default="",
        description="Full webhook URL for Telegram"
    )
    
    # Database Configuration
    DATABASE_URL: PostgresDsn = Field(
        ...,
        description="PostgreSQL database connection URL"
    )
    DB_POOL_SIZE: int = Field(
        default=20,
        description="Database connection pool size"
    )
    DB_MAX_OVERFLOW: int = Field(
        default=10,
        description="Maximum overflow connections"
    )
    
    # Redis Configuration
    REDIS_URL: RedisDsn = Field(
        ...,
        description="Redis connection URL for caching and sessions"
    )
    REDIS_MAX_CONNECTIONS: int = Field(
        default=50,
        description="Maximum Redis connections"
    )
    
    # AI/ML Configuration
    OPENAI_API_KEY: str = Field(
        default="",
        description="OpenAI API key — leave blank to use Ollama (local) as the default AI provider"
    )
    OPENAI_MODEL: str = Field(
        default="gpt-4o-mini",
        description="OpenAI model to use when OPENAI_API_KEY is set"
    )
    
    # Payment Gateway - Stripe
    STRIPE_SECRET_KEY: str = Field(
        ...,
        description="Stripe secret API key"
    )
    STRIPE_WEBHOOK_SECRET: str = Field(
        ...,
        description="Stripe webhook signing secret"
    )
    STRIPE_PUBLISHABLE_KEY: str = Field(
        default="",
        description="Stripe publishable key (optional for backend)"
    )
    
    # Payment Gateway - Paystack
    PAYSTACK_SECRET_KEY: str = Field(
        ...,
        description="Paystack secret API key"
    )
    PAYSTACK_WEBHOOK_SECRET: str = Field(
        default="",
        description="Paystack webhook secret"
    )
    
    # Payment Gateway - PayPal
    PAYPAL_CLIENT_ID: str = Field(
        ...,
        description="PayPal client ID"
    )
    PAYPAL_SECRET: str = Field(
        ...,
        description="PayPal secret key"
    )
    PAYPAL_MODE: Literal["sandbox", "live"] = Field(
        default="sandbox",
        description="PayPal environment mode"
    )
    PAYPAL_WEBHOOK_ID: str = Field(
        default="",
        description="PayPal webhook ID for signature verification"
    )

    # Ollama (default local AI provider)
    OLLAMA_BASE_URL: str = Field(
        default="http://localhost:11434",
        description="Ollama API base URL"
    )
    OLLAMA_MODEL: str = Field(
        default="llama3",
        description="Ollama model to use (must be pulled: ollama pull llama3)"
    )
    
    # API Configuration
    API_V1_PREFIX: str = Field(
        default="/api/v1",
        description="API version 1 route prefix"
    )
    PAYMENT_CLIENT_BASE_URL: str = Field(
        default="http://localhost:8000",
        description="Base URL for payment checkout client",
    )
    PAYMENT_CLIENT_CHECKOUT_PATH: str = Field(
        default="/payments",
        description="Checkout route path in payment client app",
    )
    ALLOWED_ORIGINS: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        description="CORS allowed origins"
    )
    
    # Security
    SECRET_KEY: str = Field(
        ...,
        description="Secret key for JWT and session encryption"
    )
    ALGORITHM: str = Field(
        default="HS256",
        description="JWT algorithm"
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30,
        description="JWT access token expiration time in minutes"
    )
    
    # Task Scheduler
    ENABLE_SCHEDULER: bool = Field(
        default=True,
        description="Enable background task scheduler"
    )
    WEEKLY_REPORT_CRON: str = Field(
        default="0 9 * * MON",
        description="Cron expression for weekly reports (every Monday at 9 AM)"
    )
    
    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.ENV == "production"
    
    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.ENV == "development"


# Global settings instance
# Import and use this throughout the application
settings = Settings()
