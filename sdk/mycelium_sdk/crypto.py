"""
Wallet secret encryption/decryption for Mycelium.

This is the single source of truth for the at-rest wallet format. It is used by
BOTH the CLI (`mycelium newwallet`, which encrypts a freshly generated Ed25519
seed) and the SDK (`AgentContext`, which decrypts that seed to sign live Soroban
transactions). Keeping it here means the crypto is never duplicated and the two
sides can never drift.

Scheme (per sdk.md "Encrypted Keys" constraint):
  - Key derivation: PBKDF2-HMAC-SHA256, 600,000 iterations, 32-byte key.
  - Symmetric cipher: AES-256-GCM (authenticated) with a 12-byte nonce.
  - Salt: 16 random bytes per wallet.

The plaintext that gets encrypted is the Stellar secret SEED string (the
`S...` value from `Keypair.secret`), so decryption yields something directly
usable with `stellar_sdk.Keypair.from_secret`.

On-disk wallet payload (`.mycelium/wallet.json`):
    {
      "public_key":       "G...",          # plaintext G-address
      "encrypted_secret": "<hex>",         # AES-GCM ciphertext + tag
      "nonce":            "<hex>",         # 12-byte GCM nonce
      "salt":             "<hex>"          # 16-byte PBKDF2 salt
    }
"""

import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --- Tunables (changing these breaks existing wallets) -----------------------
PBKDF2_ITERATIONS = 600_000
KEY_LENGTH = 32   # AES-256
SALT_LENGTH = 16
NONCE_LENGTH = 12  # GCM standard

# Environment variable used as a fallback passphrase source so that
# non-interactive flows (CI, `mycelium deploy` in a pipeline) can decrypt.
PASSPHRASE_ENV_VAR = "MYCELIUM_DECRYPT_KEY"


class WalletDecryptionError(Exception):
    """Raised when a wallet secret cannot be decrypted (bad passphrase / tampered file)."""


def resolve_passphrase(explicit: str | None = None) -> str:
    """
    Resolve the encryption passphrase from, in order:
      1. an explicit argument,
      2. the MYCELIUM_DECRYPT_KEY environment variable.
    Raises ValueError if neither is available.
    """
    if explicit:
        return explicit
    env_value = os.environ.get(PASSPHRASE_ENV_VAR)
    if env_value:
        return env_value
    raise ValueError(
        "No passphrase provided. Pass one explicitly or set the "
        f"{PASSPHRASE_ENV_VAR} environment variable."
    )


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from a passphrase + salt via PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_secret(secret_seed: str, passphrase: str) -> dict:
    """
    Encrypt a Stellar secret seed string ('S...') under `passphrase`.

    Returns the hex-encoded fields ready to be merged into the wallet JSON:
    `{"encrypted_secret", "nonce", "salt"}`.
    """
    salt = os.urandom(SALT_LENGTH)
    nonce = os.urandom(NONCE_LENGTH)
    key = derive_key(passphrase, salt)
    ciphertext = AESGCM(key).encrypt(nonce, secret_seed.encode("utf-8"), None)
    return {
        "encrypted_secret": ciphertext.hex(),
        "nonce": nonce.hex(),
        "salt": salt.hex(),
    }


def decrypt_secret(
    encrypted_secret_hex: str,
    nonce_hex: str,
    salt_hex: str,
    passphrase: str,
) -> str:
    """
    Decrypt the wallet secret seed. Returns the plaintext 'S...' seed string.

    Raises WalletDecryptionError on an incorrect passphrase or tampered payload
    (AES-GCM authentication failure).
    """
    try:
        salt = bytes.fromhex(salt_hex)
        nonce = bytes.fromhex(nonce_hex)
        ciphertext = bytes.fromhex(encrypted_secret_hex)
    except ValueError as exc:
        raise WalletDecryptionError(f"Malformed wallet payload: {exc}") from exc

    key = derive_key(passphrase, salt)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:  # cryptography raises InvalidTag; normalize it
        raise WalletDecryptionError(
            "Failed to decrypt wallet secret — wrong passphrase or corrupted file."
        ) from exc
    return plaintext.decode("utf-8")
