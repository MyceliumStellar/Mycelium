"""Offline tests for the wallet crypto module."""

import os

import pytest
from stellar_sdk import Keypair

from mycelium_sdk import crypto


def test_encrypt_decrypt_round_trip():
    kp = Keypair.random()
    enc = crypto.encrypt_secret(kp.secret, "correct horse")
    assert set(enc) == {"encrypted_secret", "nonce", "salt"}
    back = crypto.decrypt_secret(enc["encrypted_secret"], enc["nonce"], enc["salt"], "correct horse")
    assert back == kp.secret


def test_wrong_passphrase_raises():
    enc = crypto.encrypt_secret(Keypair.random().secret, "right")
    with pytest.raises(crypto.WalletDecryptionError):
        crypto.decrypt_secret(enc["encrypted_secret"], enc["nonce"], enc["salt"], "wrong")


def test_each_encryption_is_unique():
    secret = Keypair.random().secret
    a = crypto.encrypt_secret(secret, "pw")
    b = crypto.encrypt_secret(secret, "pw")
    # Random salt + nonce mean ciphertexts differ even for identical input.
    assert a["encrypted_secret"] != b["encrypted_secret"]
    assert a["nonce"] != b["nonce"]


def test_resolve_passphrase_explicit_and_env(monkeypatch):
    assert crypto.resolve_passphrase("explicit") == "explicit"
    monkeypatch.setenv(crypto.PASSPHRASE_ENV_VAR, "from-env")
    assert crypto.resolve_passphrase() == "from-env"
    monkeypatch.delenv(crypto.PASSPHRASE_ENV_VAR, raising=False)
    with pytest.raises(ValueError):
        crypto.resolve_passphrase()
