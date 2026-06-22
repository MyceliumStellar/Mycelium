"""
mycelium.toml read/write helpers.

Reading uses the stdlib `tomllib` (Python 3.11+); writing uses `tomli-w`. We
only ever patch flat values (e.g. `onchain.contract_id` after deploy), so the
lack of comment preservation is acceptable.
"""

import os
import tomllib

import tomli_w

DEFAULT_CONFIG_PATH = "mycelium.toml"


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load and parse a mycelium.toml file. Raises FileNotFoundError if absent."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Run `mycelium init <name>` first, or run from a project directory."
        )
    with open(path, "rb") as f:
        return tomllib.load(f)


def save_config(data: dict, path: str = DEFAULT_CONFIG_PATH) -> None:
    """Serialize a config dict back to TOML."""
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def set_value(table: str, key: str, value, path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Set `[table].key = value` in the config file, creating the table if needed."""
    data = load_config(path)
    data.setdefault(table, {})[key] = value
    save_config(data, path)
    return data


def get_value(table: str, key: str, default=None, path: str = DEFAULT_CONFIG_PATH):
    """Read `[table].key`, returning `default` if missing."""
    try:
        data = load_config(path)
    except FileNotFoundError:
        return default
    return data.get(table, {}).get(key, default)
