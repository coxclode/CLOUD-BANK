"""
CLOUD BANK — Configuración Central del Sistema
Carga y valida todas las variables de entorno con tipos estrictos.
"""

from functools import lru_cache
from typing import Literal
from pydantic import Field, SecretStr, AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", extra="ignore")

    provider: str = Field(default="anthropic", pattern=r"^(anthropic|openai|gemini)$")
    primary_model: str = Field(default="claude-sonnet-4-6")
    fast_model: str = Field(default="claude-haiku-4-5-20251001")
    max_tokens: int = Field(default=4096, ge=512, le=8192)
    temperature: float = Field(default=0.0, ge=0.0, le=0.3)
    timeout_seconds: int = Field(default=30, ge=5, le=120)


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    url: SecretStr = Field(default="redis://localhost:6379/0")
    checkpoint_db: int = Field(default=1)
    cache_db: int = Field(default=2)
    ttl_seconds: int = Field(default=3600)
    max_connections: int = Field(default=50)


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    secret_key: SecretStr = Field(alias="SECRET_KEY")
    encryption_key: SecretStr = Field(alias="ENCRYPTION_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=30, alias="JWT_EXPIRE_MINUTES")
    api_key_header: str = Field(default="X-CloudBank-API-Key", alias="API_KEY_HEADER")
    internal_api_key: SecretStr = Field(alias="INTERNAL_API_KEY")
    rate_limit_requests: int = Field(default=100, alias="RATE_LIMIT_REQUESTS")
    rate_limit_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SECONDS")
    max_payload_bytes: int = Field(default=1_048_576, alias="MAX_PAYLOAD_BYTES")


class RiskThresholds(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    fraud_critical_threshold: float = Field(default=0.85, alias="FRAUD_CRITICAL_THRESHOLD")
    fraud_high_threshold: float = Field(default=0.65, alias="FRAUD_HIGH_THRESHOLD")
    default_probability_reject: float = Field(default=0.70, alias="DEFAULT_PROBABILITY_REJECT")
    default_probability_review: float = Field(default=0.45, alias="DEFAULT_PROBABILITY_REVIEW")
    credit_score_minimum: int = Field(default=550, alias="CREDIT_SCORE_MINIMUM")
    capacity_ratio_maximum: float = Field(default=0.40, alias="CAPACITY_RATIO_MAXIMUM")


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    fraud_agent_timeout: int = Field(default=20, alias="FRAUD_AGENT_TIMEOUT")
    credit_agent_timeout: int = Field(default=15, alias="CREDIT_AGENT_TIMEOUT")
    actuarial_agent_timeout: int = Field(default=15, alias="ACTUARIAL_AGENT_TIMEOUT")
    approval_agent_timeout: int = Field(default=10, alias="APPROVAL_AGENT_TIMEOUT")
    max_retries_per_agent: int = Field(default=3, ge=1, le=5, alias="MAX_RETRIES_PER_AGENT")
    retry_backoff_seconds: float = Field(default=2.0, alias="RETRY_BACKOFF_SECONDS")


class ExternalServices(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    credit_bureau_url: AnyHttpUrl = Field(alias="CREDIT_BUREAU_URL")
    credit_bureau_api_key: SecretStr = Field(alias="CREDIT_BUREAU_API_KEY")
    biometric_service_url: AnyHttpUrl = Field(alias="BIOMETRIC_SERVICE_URL")
    biometric_api_key: SecretStr = Field(alias="BIOMETRIC_API_KEY")
    device_intelligence_url: AnyHttpUrl = Field(alias="DEVICE_INTELLIGENCE_URL")
    device_api_key: SecretStr = Field(alias="DEVICE_API_KEY")
    ip_reputation_url: AnyHttpUrl = Field(alias="IP_REPUTATION_URL")
    ip_api_key: SecretStr = Field(alias="IP_API_KEY")
    aml_service_url: AnyHttpUrl = Field(alias="AML_SERVICE_URL")
    aml_api_key: SecretStr = Field(alias="AML_API_KEY")
    human_queue_url: AnyHttpUrl = Field(alias="HUMAN_QUEUE_URL")
    human_queue_api_key: SecretStr = Field(alias="HUMAN_QUEUE_API_KEY")


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    otel_endpoint: str = Field(default="http://localhost:4317", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    service_name: str = Field(default="cloudbank-credit-engine", alias="OTEL_SERVICE_NAME")
    environment: str = Field(default="production", alias="OTEL_ENVIRONMENT")
    prometheus_port: int = Field(default=9090, alias="PROMETHEUS_PORT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO", alias="LOG_LEVEL")
    log_format: Literal["json", "console"] = Field(default="json", alias="LOG_FORMAT")


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "staging", "production"] = Field(
        default="production", alias="APP_ENV"
    )
    port: int = Field(default=8000, alias="APP_PORT")
    host: str = Field(default="0.0.0.0", alias="APP_HOST")
    workers: int = Field(default=4, ge=1, alias="APP_WORKERS")
    debug: bool = Field(default=False, alias="DEBUG")
    cors_origins: list[str] = Field(default=[], alias="CORS_ORIGINS")
    # Claves de proveedor LLM — solo se exige la del proveedor activo (LLM_PROVIDER),
    # verificado en tiempo de uso por infrastructure/llm/provider_factory.py, no al arrancar.
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), alias="ANTHROPIC_API_KEY")
    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    gemini_api_key: SecretStr = Field(default=SecretStr(""), alias="GEMINI_API_KEY")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def llm(self) -> LLMSettings:
        return LLMSettings()

    @property
    def redis(self) -> RedisSettings:
        return RedisSettings()

    @property
    def security(self) -> SecuritySettings:
        return SecuritySettings()

    @property
    def risk(self) -> RiskThresholds:
        return RiskThresholds()

    @property
    def agents(self) -> AgentSettings:
        return AgentSettings()

    @property
    def observability(self) -> ObservabilitySettings:
        return ObservabilitySettings()

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()
