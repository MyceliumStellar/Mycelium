import base64
from datetime import datetime, timedelta

from jose import jwt, JWTError
from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ide.backend.config import JWT_SECRET_KEY, ALGORITHM, TOKEN_ENCRYPTION_KEY


def create_access_token(data: dict, expires_delta: timedelta = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=180)  # 3 hours
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return {}


# ── At-rest secret encryption ────────────────────────────────────────────────
# Stored secrets (GitHub tokens, user API keys) are encrypted with a key derived
# from the DEDICATED TOKEN_ENCRYPTION_KEY via HKDF-SHA256 — NOT from the JWT
# signing key, so leaking the JWT key does not expose stored credentials. The
# old scheme null-padded JWT_SECRET_KEY; we keep it ONLY as a fallback decryptor
# (via MultiFernet) so credentials written before this change still decrypt and
# get re-encrypted under the new key on the user's next login.
_HKDF_SALT = b"mycelium.ide.token-encryption.v1"  # fixed, non-secret domain separator
_HKDF_INFO = b"fernet-key"


def _derive_fernet_key(secret: str) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=_HKDF_INFO)
    return base64.urlsafe_b64encode(hkdf.derive(secret.encode("utf-8")))


def _legacy_fernet_key() -> bytes:
    # Deprecated: 32-byte null-padded JWT_SECRET_KEY. Decrypt-only, never used
    # for new ciphertext. Remove once all stored credentials have rotated.
    key = JWT_SECRET_KEY.encode().ljust(32, b"\0")[:32]
    return base64.urlsafe_b64encode(key)


def _fernet() -> MultiFernet:
    # MultiFernet encrypts with the FIRST key (the new, HKDF-derived one) and
    # decrypts by trying each key in order — giving transparent migration.
    primary = Fernet(_derive_fernet_key(TOKEN_ENCRYPTION_KEY))
    legacy = Fernet(_legacy_fernet_key())
    return MultiFernet([primary, legacy])


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    return _fernet().decrypt(encrypted_token.encode()).decode()
