import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID

from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: UUID) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> UUID | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None
    subject = payload.get("sub")
    if subject is None:
        return None
    try:
        return UUID(subject)
    except ValueError:
        return None


@lru_cache
def _fernet() -> Fernet:
    """Derive a Fernet key from the JWT secret so provider API keys can be
    encrypted at rest without a separate secret to manage.
    """
    settings = get_settings()
    digest = hashlib.sha256(settings.jwt_secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


# API keys (app/models/api_key.py) — a self-service credential for
# external/machine callers, distinct from the JWT bearer flow above. The
# prefix lets get_current_user_flexible (app/api/deps.py) tell an API key
# apart from a JWT at a glance, without needing to attempt-and-fail a JWT
# decode first; it's also what a leaked-key scanner would grep for.
API_KEY_PREFIX = "sk_live_"


def generate_api_key() -> tuple[str, str, str]:
    """One freshly-generated key: (full_key — shown to the caller exactly
    once, display_prefix — stored and shown in the list UI forever,
    key_hash — the only form actually persisted). The full key is never
    stored anywhere, mirroring how a password is never stored — only
    verifiable, never recoverable.
    """
    full_key = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    display_prefix = full_key[: len(API_KEY_PREFIX) + 6] + "…"
    return full_key, display_prefix, hash_api_key(full_key)


def hash_api_key(key: str) -> str:
    """Plain sha256, not bcrypt/passlib — `key` is already a high-entropy
    random token we generated (secrets.token_urlsafe), not a low-entropy
    user-chosen secret, so there's no offline-brute-force case a slow
    hash defends against here; sha256 is the standard approach for this
    (same as GitHub/Stripe API keys) and is fast enough to hash on every
    authenticated request without adding real latency.
    """
    return hashlib.sha256(key.encode()).hexdigest()
