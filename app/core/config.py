from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VISION_", env_file=".env", extra="ignore")

    environment: str = "development"
    engine_version: str = "0.1.0"
    database_url: str = "postgresql+psycopg://vision:vision@localhost:5434/vision"

    # Secretos: sin valor por defecto, el servicio no arranca sin ellos.
    secret_key: SecretStr
    service_api_keys: SecretStr  # claves del backend de Carpetazo, separadas por coma

    session_ttl_seconds: int = 900

    max_image_bytes: int = 4_000_000
    min_image_side: int = 320
    max_image_side: int = 4096
    max_image_pixels: int = 16_000_000

    scan_rate_limit_per_minute: int = 120
    max_concurrent_scans: int = 2
    scan_queue_timeout_seconds: float = 3.0
    scan_result_ttl_seconds: int = 300
    scan_store_max_entries: int = 500

    # Umbrales provisionales: no están calibrados con fotos reales (ver evaluation/).
    min_sharpness: float = 40.0
    min_brightness: float = 40.0
    max_brightness: float = 220.0

    @property
    def service_keys(self) -> list[str]:
        return [k.strip() for k in self.service_api_keys.get_secret_value().split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
