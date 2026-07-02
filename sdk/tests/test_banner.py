"""Tests for the dynamic banner rendering/scaling behavior."""

import os
import shutil
from unittest.mock import patch
import pytest

from mycelium_sdk import banner


def test_banner_scales_large():
    with patch("shutil.get_terminal_size", return_value=os.terminal_size((80, 24))):
        output = banner.render(color=False)
        assert "███╗" in output
        assert " __  __ " not in output
        assert "[ MYCELIUM ]" not in output


def test_banner_scales_medium():
    with patch("shutil.get_terminal_size", return_value=os.terminal_size((60, 24))):
        output = banner.render(color=False)
        assert "███╗" not in output
        assert " __  __ " in output
        assert "[ MYCELIUM ]" not in output


def test_banner_scales_small():
    with patch("shutil.get_terminal_size", return_value=os.terminal_size((40, 24))):
        output = banner.render(color=False)
        assert "███╗" not in output
        assert " __  __ " not in output
        assert "[ MYCELIUM ]" in output
