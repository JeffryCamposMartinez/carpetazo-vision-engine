from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VISION_", env_file=".env", extra="ignore")

    environment: str = "development"
    engine_version: str = "0.1.0"
    database_url: str = "postgresql+psycopg://vision:vision@localhost:5434/vision"

    # Secretos: sin valor por defecto, el servicio no arranca sin ellos.
    # Obligatorios para la API (create_app lo comprueba); el CLI no los necesita.
    secret_key: SecretStr | None = None
    service_api_keys: SecretStr | None = None  # claves del backend de Carpetazo, separadas por coma

    session_ttl_seconds: int = 900

    # Interfaz de demostración local (app/demo_ui, servida en /demo). Desactivada por defecto; no usar en producción.
    demo_ui_enabled: bool = False

    # Embeddings visuales (E3). DINOv2-small corre en un proceso aparte (entorno ML independiente; sin torch aquí).
    embedding_primary: str = "dinov2-small"  # dinov2-small | grid
    embedding_ml_python: str | None = None  # por defecto ~/vision-ml-lab/venv/Scripts/python.exe
    embedding_ml_home: str | None = None  # por defecto ~/vision-ml-lab/hf-home (pesos ya descargados; sin red)
    embedding_device: str = "auto"  # auto | cuda | cpu
    embedding_min_free_vram_mb: int = 700  # auto usa la GPU solo si queda al menos esta VRAM libre (GTX 960 de 2 GB)
    embedding_worker_idle_seconds: float = 120.0  # descarga el modelo (libera VRAM) tras este tiempo sin uso
    embedding_worker_startup_timeout: float = 120.0
    embedding_request_timeout: float = 60.0
    reference_search_backend: str = "exact"  # exact (NumPy, por defecto) | pgvector (opcional)
    embedding_retry_cooldown_seconds: float = 60.0  # tras un fallo del primario se usa el respaldo este tiempo

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

    # Proveedor de catálogo (TCGdex). Solo se llama desde el CLI, nunca al arrancar la API.
    tcgdex_base_url: str = "https://api.tcgdex.net/v2"
    provider_min_interval_seconds: float = 0.1  # ~10 peticiones/s como máximo
    provider_timeout_seconds: float = 15.0
    provider_max_retries: int = 3
    provider_contact: str = ""  # se añade al User-Agent para que el proveedor pueda contactarnos

    # Las imágenes de TCGdex pertenecen a The Pokémon Company y no hay permiso verificado:
    # cambiar a "permitted" solo con autorización escrita.
    tcgdex_image_license_status: str = "unknown"
    image_allowed_hosts: str = "assets.tcgdex.net"
    image_max_bytes: int = 3_000_000
    image_min_side: int = 200
    image_max_side: int = 4000

    image_storage_backend: str = "local"  # local | s3
    image_storage_path: str = "./datasets/images"
    r2_account_id: str | None = None  # si se define y no hay s3_endpoint_url, se deriva el endpoint de R2
    s3_endpoint_url: str | None = None  # R2: https://<account>.r2.cloudflarestorage.com
    s3_bucket: str | None = None
    s3_region: str = "auto"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    @property
    def service_keys(self) -> list[str]:
        if self.service_api_keys is None:
            return []
        return [k.strip() for k in self.service_api_keys.get_secret_value().split(",") if k.strip()]

    @property
    def resolved_s3_endpoint(self) -> str | None:
        if self.s3_endpoint_url:
            return self.s3_endpoint_url
        if self.r2_account_id:
            return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
