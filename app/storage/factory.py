from app.core.config import Settings
from app.storage.base import ObjectStorage, StorageError
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


def get_storage(settings: Settings) -> ObjectStorage:
    if settings.image_storage_backend == "s3":
        return S3Storage.from_settings(settings)
    if settings.image_storage_backend == "local":
        return LocalStorage(settings.image_storage_path)
    raise StorageError("VISION_IMAGE_STORAGE_BACKEND debe ser 'local' o 's3'")
