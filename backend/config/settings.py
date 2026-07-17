"""
Configuración centralizada — CLOUD BANK

Single source of truth para toda la configuración del sistema.
Usa Pydantic Settings para:
  - Validación de tipos en tiempo de arranque
  - Carga desde variables de entorno / .env
  - Valores por defecto seguros
  - Documentación inline

Prefijos de env vars:
  CLOUDBANK_APP_*        → Configuración de la aplicación
  CLOUDBANK_LLM_*        → Proveedor LLM (Anthropic)
  CLOUDBANK_REDIS_*      → Redis
  CLOUDBANK_DATABASE_*   → PostgreSQL
  CLOUDBANK_SECURITY_*   → Parámetros de seguridad
  CLOUDBANK_AGENTS_*     → Configuración de agentes
  CLOUDBANK_EXTERNAL_*   → Servicios externos (bureau, AML)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import AnyUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOUDBANK_APP_", env_file=".env", extra="ignore")

    name:             str = "CLOUD BANK"
    version:          str = "2.0.0"
    environment:      str = Field(default="development", pattern=r"^(development|staging|production)$")
    log_level:        str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    allowed_origins:  list[str] = Field(default=["*"])
    allowed_hosts:    list[str] = Field(default=["*"])
    max_payload_size: int = 1_048_576  # 1 MB


class AIServicesSettings(BaseSettings):
    """
    ai-services es el único componente que llama a un LLM — el backend nunca
    lo hace directamente. Aquí solo vive la ubicación del servicio HTTP interno.
    """
    model_config = SettingsConfigDict(env_prefix="CLOUDBANK_AI_SERVICES_", env_file=".env", extra="ignore")

    url:           str   = "http://localhost:8100"
    timeout_secs:  int   = Field(default=90, ge=5, le=300)


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOUDBANK_REDIS_", env_file=".env", extra="ignore")

    url:            AnyUrl   = "redis://localhost:6379/0"
    password:       SecretStr = SecretStr("")
    max_connections: int     = Field(default=20, ge=1)
    socket_timeout:  int     = Field(default=5, ge=1)
    key_prefix:      str     = "cloudbank:"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOUDBANK_DATABASE_", env_file=".env", extra="ignore")

    url:             AnyUrl   = "postgresql://cloudbank:password@localhost:5432/cloudbank"
    pool_min_size:   int      = Field(default=5, ge=1)
    pool_max_size:   int      = Field(default=20, ge=1)
    command_timeout: int      = Field(default=30, ge=5)
    ssl_mode:        str      = "prefer"


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOUDBANK_SECURITY_", env_file=".env", extra="ignore")

    encryption_key:         SecretStr = SecretStr("dev-key-change-in-production-32c")
    vault_url:              str       = "http://vault:8200"
    vault_token:            SecretStr = SecretStr("")
    vault_mount:            str       = "cloudbank"
    jwt_secret:             SecretStr = SecretStr("")
    jwt_expiry_minutes:     int       = Field(default=60, ge=1)
    rate_limit_default_rpm: int       = Field(default=60, ge=1)
    rate_limit_evaluate_rpm: int      = Field(default=10, ge=1)
    max_payload_bytes:      int       = Field(default=1_048_576, ge=1024)

    # Login demo para el scaffold de frontend (Fase 1 académica). NO es un IdP real —
    # ver docs/FREE_TIER_ARCHITECTURE.md §11 para la migración a Clerk/OAuth2.
    demo_officer_username:     str       = "officer"
    demo_officer_password_hash: SecretStr = SecretStr("")
    demo_officer_role:          str       = "risk_officer"


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOUDBANK_AGENTS_", env_file=".env", extra="ignore")

    max_retries:             int   = Field(default=2, ge=0, le=5)
    timeout_seconds:         float = Field(default=60.0, ge=5.0, le=300.0)
    self_correction_max_iter: int  = Field(default=3, ge=1, le=5)
    min_confidence_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    min_quality_threshold:    float = Field(default=0.40, ge=0.0, le=1.0)
    parallel_tool_timeout:    float = Field(default=10.0, ge=1.0, le=30.0)


class ExternalServicesSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOUDBANK_EXTERNAL_", env_file=".env", extra="ignore")

    credit_bureau_url:  str = "http://mock-credit-bureau:8001"
    aml_service_url:    str = "http://mock-aml-service:8002"
    biometric_url:      str = "http://mock-biometric:8003"
    income_verify_url:  str = "http://mock-income:8004"
    device_intel_url:   str = "http://mock-device:8005"
    http_timeout:       int = Field(default=10, ge=1, le=60)

    # RENIEC (Perú) vía Decolecta — consulta de identidad por DNI
    reniec_api_url: str = "https://api.decolecta.com/v1/reniec/dni"
    reniec_api_key: SecretStr = SecretStr("")


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLOUDBANK_OBS_", env_file=".env", extra="ignore")

    otlp_endpoint:    str  = "http://jaeger:4317"
    service_name:     str  = "cloudbank-credit-engine"
    sample_rate:      float = Field(default=1.0, ge=0.0, le=1.0)
    metrics_enabled:  bool  = True
    tracing_enabled:  bool  = True


class Settings(BaseSettings):
    """Configuración raíz que agrega todas las sub-configuraciones."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app:          AppSettings          = AppSettings()
    ai_services:  AIServicesSettings   = AIServicesSettings()
    redis:        RedisSettings        = RedisSettings()
    database:     DatabaseSettings     = DatabaseSettings()
    security:     SecuritySettings     = SecuritySettings()
    agents:       AgentSettings        = AgentSettings()
    external:     ExternalServicesSettings = ExternalServicesSettings()
    observability: ObservabilitySettings  = ObservabilitySettings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Singleton de configuración. Se inicializa una vez y se cachea.
    En tests, limpiar el cache con get_settings.cache_clear().
    """
    return Settings()
