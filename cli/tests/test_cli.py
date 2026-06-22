"""Offline tests for the CLI commands (scaffolding, wallet, config)."""

import json
import os

import pytest

from mycelium_cli.commands.init import run_init, validate_unique_name
from mycelium_cli.commands.newwallet import run_newwallet
from mycelium_cli import config


# ── init ─────────────────────────────────────────────────────────────────────
def test_init_scaffolds_spec_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_init("demo", framework="langgraph", model="gemini-x", unique_name="demo_agent")

    for rel in ("mycelium.toml", "agent.py", "contract.py", ".gitignore"):
        assert (tmp_path / "demo" / rel).exists()
    assert (tmp_path / "demo" / ".mycelium").is_dir()

    cfg = config.load_config(str(tmp_path / "demo" / "mycelium.toml"))
    assert cfg["agent"]["framework"] == "langgraph"
    assert cfg["agent"]["unique_name"] == "demo_agent"
    assert {"project", "agent", "onchain", "registry"} <= set(cfg)


def test_init_writes_env_with_api_key_for_gemini(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_init("demo", framework="gemini", model="gemini-2.5-flash",
             unique_name="demo_agent", api_key="SECRET123")

    env = (tmp_path / "demo" / ".env").read_text()
    assert "GEMINI_API_KEY=SECRET123" in env
    # The key file and the .env entry must be gitignored.
    assert ".env" in (tmp_path / "demo" / ".gitignore").read_text()
    # The gemini agent template must read the key from the environment, not hard-code it.
    agent_src = (tmp_path / "demo" / "agent.py").read_text()
    assert "SECRET123" not in agent_src
    assert "GEMINI_API_KEY" in agent_src


def test_init_rejects_bad_framework(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        run_init("demo", framework="not-a-framework")


def test_init_rejects_bad_unique_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        run_init("demo", unique_name="ab")  # too short


@pytest.mark.parametrize("name,ok", [
    ("abc", True), ("ag_ent_1", True), ("A" * 30, True),
    ("ab", False), ("A" * 31, False), ("has space", False), ("dash-no", False),
])
def test_unique_name_regex(name, ok):
    assert validate_unique_name(name) is ok


# ── newwallet ────────────────────────────────────────────────────────────────
def test_newwallet_round_trips_to_keypair(tmp_path):
    from mycelium_sdk import crypto
    from stellar_sdk import Keypair

    path = tmp_path / ".mycelium" / "wallet.json"
    pub = run_newwallet(path=str(path), passphrase="pw")

    payload = json.loads(path.read_text())
    assert set(payload) == {"public_key", "encrypted_secret", "nonce", "salt"}
    seed = crypto.decrypt_secret(
        payload["encrypted_secret"], payload["nonce"], payload["salt"], "pw"
    )
    assert Keypair.from_secret(seed).public_key == pub == payload["public_key"]


def test_newwallet_refuses_overwrite(tmp_path):
    path = tmp_path / "wallet.json"
    run_newwallet(path=str(path), passphrase="pw")
    with pytest.raises(FileExistsError):
        run_newwallet(path=str(path), passphrase="pw")
    # force overwrites
    run_newwallet(path=str(path), passphrase="pw", force=True)


# ── config ───────────────────────────────────────────────────────────────────
def test_config_round_trip(tmp_path):
    p = str(tmp_path / "mycelium.toml")
    config.save_config({"onchain": {"contract_id": ""}}, p)
    config.set_value("onchain", "contract_id", "CABC", p)
    assert config.get_value("onchain", "contract_id", path=p) == "CABC"


def test_get_value_missing_file_returns_default():
    assert config.get_value("x", "y", default="d", path="/no/such/mycelium.toml") == "d"
