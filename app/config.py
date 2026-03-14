from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "ira"
    redis_uri: str = "redis://localhost:6379"

    # Worker pools
    worker_pool_priority_size: int = 20
    worker_pool_general_size: int = 50
    worker_pool_overflow_size: int = 30

    # Rate limits (per minute / per day)
    rate_limit_free_per_min: int = 10
    rate_limit_free_per_day: int = 100
    rate_limit_premium_per_min: int = 60
    rate_limit_premium_per_day: int = 1000
    rate_limit_enterprise_per_min: int = 200
    rate_limit_enterprise_per_day: int = 0  # 0 = unlimited

    # Analytics
    analytics_batch_size: int = 100
    analytics_flush_interval: float = 5.0
    analytics_queue_size: int = 10000

    # Logging
    log_level: str = "INFO"

    # Slow operation threshold (ms)
    slow_op_threshold_ms: int = 100

    # Graceful shutdown timeout (seconds)
    shutdown_timeout: int = 30

    model_config = {"env_prefix": "IRA_", "env_file": ".env"}


settings = Settings()
