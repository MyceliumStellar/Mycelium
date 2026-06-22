"""
`mycelium newwallet` — generate an encrypted Ed25519 wallet (sdk.md section 2.2).

Generates a Stellar keypair, encrypts the secret seed with AES-GCM-256 (key
derived from a passphrase via PBKDF2), and writes the payload to
`.mycelium/wallet.json`. The plaintext seed is never written to disk.
"""

import json
import os

from mycelium_sdk import crypto

DEFAULT_WALLET_PATH = os.path.join(".mycelium", "wallet.json")


def run_newwallet(
    path: str = DEFAULT_WALLET_PATH,
    passphrase: str | None = None,
    force: bool = False,
) -> str:
    """
    Create an encrypted wallet at `path`. `passphrase` is resolved via
    crypto.resolve_passphrase (explicit arg → MYCELIUM_DECRYPT_KEY). Refuses to
    overwrite an existing wallet unless `force=True`. Returns the public key.
    """
    from stellar_sdk import Keypair

    if os.path.exists(path) and not force:
        raise FileExistsError(
            f"A wallet already exists at {path}. Use --force to overwrite it "
            "(this destroys the existing key)."
        )

    pw = crypto.resolve_passphrase(passphrase)
    keypair = Keypair.random()
    payload = {"public_key": keypair.public_key, **crypto.encrypt_secret(keypair.secret, pw)}

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
        try:  # exist_ok won't fix the mode of a pre-existing dir
            os.chmod(parent, 0o700)
        except OSError:
            pass

    # Create the file with 0600 from the start so the ciphertext is never
    # world-readable, even for the instant between write and chmod.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
    except (OSError, ValueError):  # platforms without full POSIX mode support
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    return keypair.public_key
