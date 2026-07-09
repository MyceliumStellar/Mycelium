__version__ = "0.5.1"

from mycelium_sdk.context import AgentContext, StellarNetwork, TxResult
from mycelium_sdk.hive import HiveClient
from mycelium_sdk.x402.settlement import EscrowPaymentRouter, EscrowPaymentManager
from mycelium_sdk.jobs import JobBoardClient
from mycelium_sdk.memory import AgentMemory, AnchoringPolicy
from mycelium_sdk.constants import HIVEMIND_REGISTRY_ADDRESS
from mycelium_sdk.banner import print_banner, show_startup_banner
from mycelium_sdk.agent_loop import run_agent_loop, ContractTool
from mycelium_sdk.contract_client import ContractClient
from mycelium_sdk import logging
from mycelium_sdk import scval
from mycelium_sdk.scval import (
    u32, u64, u128, i32, i64, i128, address, symbol, string, bytes_val, boolean,
)

__all__ = [
    "__version__",
    "AgentContext",
    "StellarNetwork",
    "TxResult",
    "HiveClient",
    "EscrowPaymentRouter",
    "EscrowPaymentManager",
    "JobBoardClient",
    "AgentMemory",
    "AnchoringPolicy",
    "HIVEMIND_REGISTRY_ADDRESS",
    "print_banner",
    "show_startup_banner",
    # one-call agent loop helper (collapses agent.py boilerplate)
    "run_agent_loop",
    "ContractTool",
    # typed contract client: ctx.contract(cid).add(40)
    "ContractClient",
    # structured logging (configure(quiet=True) for production agents)
    "logging",
    # width-correct Soroban value helpers (no stellar_sdk import needed)
    "scval",
    "u32", "u64", "u128", "i32", "i64", "i128",
    "address", "symbol", "string", "bytes_val", "boolean",
]
