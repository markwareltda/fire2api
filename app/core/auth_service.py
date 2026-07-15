import hmac

from .access_key_service import AccessKeyService
from .settings import get_settings


class AuthService:
    @staticmethod
    def validate_admin_token(token: str | None) -> bool:
        if not token:
            return False
        return hmac.compare_digest(token, get_settings().admin_api_key)

    @staticmethod
    def has_active_access_key() -> bool:
        return AccessKeyService.has_active_keys()

    @staticmethod
    def validate_access_token(token: str | None):
        row = AccessKeyService.validate_token(token or "")
        return row is not None, row

    @staticmethod
    def register_access_usage(key_id: int, path: str) -> None:
        AccessKeyService.register_usage(key_id, path)
