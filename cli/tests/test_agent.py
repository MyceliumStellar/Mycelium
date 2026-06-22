"""Offline tests for the `mycelium agent` runtime."""

import os

import pytest

from mycelium_cli.commands.agent import run_agent


def test_agent_runs_main_and_binds_contract(tmp_path, capsys, monkeypatch):
    script = tmp_path / "myagent.py"
    script.write_text(
        "import os\n"
        "def main():\n"
        "    print('CONTRACT=' + os.environ['MYCELIUM_CONTRACT_ID'])\n"
    )
    monkeypatch.delenv("MYCELIUM_CONTRACT_ID", raising=False)

    run_agent(str(script), "CABC123")

    out = capsys.readouterr().out
    assert "CONTRACT=CABC123" in out
    assert os.environ["MYCELIUM_CONTRACT_ID"] == "CABC123"


def test_agent_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        run_agent(str(tmp_path / "nope.py"), "C1")


def test_agent_runtime_error_exits(tmp_path):
    script = tmp_path / "bad.py"
    script.write_text("def main():\n    raise RuntimeError('boom')\n")
    with pytest.raises(SystemExit):
        run_agent(str(script), "C1")
