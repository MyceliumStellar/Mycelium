"""
Agent-to-agent on-chain communication demo (testnet).

Two independently-scaffolded Mycelium agents (testsdk = agent1, testsdk2 = agent2),
each with its own sovereign wallet and its own deployed contract, both registered
in the SAME on-chain Hive Registry. This shows them discovering and interacting
with each other purely through on-chain state:

  1. STATELESS  — each agent resolves the other's directory entry from the
     shared Hive Registry (read-only; no shared mutable state).
  2. STATEFUL   — agent2 invokes agent1's deployed contract, mutating shared
     on-chain state that agent1 then reads back (the agents coordinate through
     a persistent on-chain object).
  3. VALUE      — agent2 pays agent1 in XLM, with agent1's address discovered
     from the registry (machine-to-machine settlement).
"""

from stellar_sdk import TransactionBuilder, Asset

from mycelium import AgentContext, HiveClient, U64

AGENT1 = {
    "name": "myc_6465185c",
    "wallet": "testsdk/.mycelium/wallet.json",
    "pass": "testsdk-pass-6465",
    "contract": "CD5FRFFV6TBYSLUDSR6YG3V6POVLDUFNB4Q346WQQC4CDTIMJQ62P74S",
}
AGENT2 = {
    "name": "myc2_dd9246f1",
    "wallet": "testsdk2/.mycelium/wallet.json",
    "pass": "testsdk2-pass-9090",
    "contract": "CDTZZIWSEV5SH6WM3LB3NR3AQK3JCYHWUF7ASUWGVJ424YODPBQGDDIL",
}


def native_balance(ctx, public_key):
    acct = ctx.horizon_server.accounts().account_id(public_key).call()
    return next(b["balance"] for b in acct["balances"] if b["asset_type"] == "native")


def main():
    ctx1 = AgentContext(AGENT1["wallet"], network_type="testnet", passphrase=AGENT1["pass"])
    ctx2 = AgentContext(AGENT2["wallet"], network_type="testnet", passphrase=AGENT2["pass"])
    hive1, hive2 = HiveClient(ctx1), HiveClient(ctx2)

    print("\n========== 1. STATELESS: mutual on-chain discovery ==========")
    a1_seen_by_a2 = hive2.resolve_agent(AGENT1["name"])
    a2_seen_by_a1 = hive1.resolve_agent(AGENT2["name"])
    print(f"agent2 resolved agent1 via registry -> {a1_seen_by_a2['public_key']}")
    print(f"  endpoint: {a1_seen_by_a2['endpoint']}")
    print(f"agent1 resolved agent2 via registry -> {a2_seen_by_a1['public_key']}")
    print(f"  endpoint: {a2_seen_by_a1['endpoint']}")

    print("\n========== 2. STATEFUL: agent2 mutates agent1's shared contract ==========")
    before = ctx1.call_contract(AGENT1["contract"], "get_count", [], read_only=True)
    print(f"agent1's contract count (read by agent1): {before}")
    print("agent2 calls add(7) on agent1's contract...")
    ctx2.call_contract(AGENT1["contract"], "add", [U64(7)])
    after = ctx1.call_contract(AGENT1["contract"], "get_count", [], read_only=True)
    print(f"agent1 re-reads its contract count: {after}  (changed by agent2: +{int(after) - int(before)})")

    print("\n========== 3. VALUE: agent2 pays agent1 (address from registry) ==========")
    dest = a1_seen_by_a2["public_key"]  # discovered on-chain, not hard-coded
    bal_before = native_balance(ctx2, dest)
    print(f"agent1 balance before: {bal_before} XLM")
    src_acct = ctx2.horizon_server.load_account(ctx2.keypair.public_key)
    tx = (
        TransactionBuilder(src_acct, ctx2.network_passphrase, base_fee=100)
        .append_payment_op(destination=dest, asset=Asset.native(), amount="3")
        .set_timeout(60)
        .build()
    )
    tx.sign(ctx2.keypair)
    resp = ctx2.horizon_server.submit_transaction(tx)
    print(f"payment tx: {resp['hash']}  success={resp.get('successful')}")
    bal_after = native_balance(ctx2, dest)
    print(f"agent1 balance after:  {bal_after} XLM  (+{float(bal_after) - float(bal_before):.4f})")

    print("\n✅ Two agents discovered each other and interacted entirely on-chain.")


if __name__ == "__main__":
    main()
