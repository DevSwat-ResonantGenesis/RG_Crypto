"""Crypto Service configuration."""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"postgresql+asyncpg://{os.getenv('CRYPTO_DB_USER', os.getenv('AUTH_DB_USER', 'doadmin'))}:"
        f"{os.getenv('CRYPTO_DB_PASSWORD', os.getenv('AUTH_DB_PASSWORD', ''))}@"
        f"{os.getenv('CRYPTO_DB_HOST', os.getenv('AUTH_DB_HOST', 'db'))}:"
        f"{os.getenv('CRYPTO_DB_PORT', os.getenv('AUTH_DB_PORT', '5432'))}/"
        f"{os.getenv('CRYPTO_DB_NAME', os.getenv('AUTH_DB_NAME', 'defaultdb'))}?sslmode=require"
    )
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/1")
    
    # Blockchain configuration
    BLOCKCHAIN_SERVICE_URL: str = os.getenv("BLOCKCHAIN_SERVICE_URL", "http://blockchain_service:8000")
    
    # Stripe configuration
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    
    # Token configuration
    PLATFORM_TOKEN_SYMBOL: str = "RGT"  # Resonant Genesis Token
    PLATFORM_TOKEN_DECIMALS: int = 18
    MIN_WITHDRAWAL_AMOUNT: float = 10.0
    WITHDRAWAL_FEE_PERCENT: float = 2.5
    
    # Security
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")
    
    class Config:
        env_file = ".env"


settings = Settings()
