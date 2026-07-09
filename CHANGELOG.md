# Changelog

All notable changes to the Mycelium developer framework and smart contracts will be documented in this file.

## [0.5.1] — 2026-07-09

Patch release with developer experience improvements: startup banner version display, self-update command, and syntax error fixes for PyPI package integrity.

### 💻 Command Line Interface (CLI)
* **Version Banner:** The startup ASCII banner now always displays the current installed version (e.g. `📦  v0.5.1`) below the tagline, so developers can identify their version at a glance.
* **`mycelium update` Command:** Added a new self-update command that queries PyPI for the latest `mycelium-stellar` release, compares versions, and auto-upgrades via `pip install --upgrade`. Supports `--yes` / `-y` to skip the confirmation prompt for CI/scripted use.
* **Syntax Error Fixes:** Fixed trailing `")` syntax errors in CLI parameter declarations across `jobs.py`, `deal.py`, and `memory.py` that caused `SyntaxError: unterminated string literal` on import.

---

### 📦 Python SDK
* **Banner API Extension:** `show_startup_banner()`, `print_banner()`, and `render()` now accept an optional `version` parameter to include the version string in the banner output.
* **Version Bump:** SDK `__version__` updated from `0.4.0` to `0.5.1`.

---

### 📋 Package Versions
* `mycelium-stellar`: `0.5.0` → `0.5.1`
* `mycelium-cli`: `0.5.0` → `0.5.1`
* `mycelium-sdk`: `0.5.0` → `0.5.1`
* `mycelium-compiler`: `0.5.0` → `0.5.1`

---

## [0.5.0] — 2026-07-08

This is the official **Mainnet Release** of the Mycelium Developer Framework, upgrading all contracts, the SDK, CLI, Web IDE, and Indexer for the Stellar Public Network (Mainnet).

### 🚀 Deployed Mainnet Contracts & Verification Links
All 5 singleton contracts have been deployed and initialized on the public network using the `myceliummainnet` key (`GCT7GPSGA4OQXCN6KYUVDCZY2P4D4QHA5GCPC72XYEN3RRF36NR6D2XX`):
* **Hive Registry**: `CCFGTAAVOCU2VQNNQUJQQI3YET27PTM3GADCBYDLA6DISXUPR5CGRS5T`
  * [Stellar Expert Verification](https://stellar.expert/explorer/public/contract/CCFGTAAVOCU2VQNNQUJQQI3YET27PTM3GADCBYDLA6DISXUPR5CGRS5T)
* **Job Board**: `CABB4SSGE5NFOCH6KE4RNCA2MGHSQIFXUKS7OZ4B4GQOEJK6R4ZMP4LG`
  * [Stellar Expert Verification](https://stellar.expert/explorer/public/contract/CABB4SSGE5NFOCH6KE4RNCA2MGHSQIFXUKS7OZ4B4GQOEJK6R4ZMP4LG)
* **Memory Anchor**: `CDFXP42NITRLDGYUMJ5OT63EVWBROJTCXQR64GUSDWHY2LH3AQM2TXYP`
  * [Stellar Expert Verification](https://stellar.expert/explorer/public/contract/CDFXP42NITRLDGYUMJ5OT63EVWBROJTCXQR64GUSDWHY2LH3AQM2TXYP)
* **Verifier Registry**: `CA574F2GDVGJSITE52TFON7MA66HB6EC2IVPMXPO5OUWDAPJ5JVCSQHC`
  * [Stellar Expert Verification](https://stellar.expert/explorer/public/contract/CA574F2GDVGJSITE52TFON7MA66HB6EC2IVPMXPO5OUWDAPJ5JVCSQHC)
* **Reputation Registry**: `CB44VUD27BJN4R2VVUONP63TQ5LG523XPV4TKFF7CLC3MQBHI7DYKRBP`
  * [Stellar Expert Verification](https://stellar.expert/explorer/public/contract/CB44VUD27BJN4R2VVUONP63TQ5LG523XPV4TKFF7CLC3MQBHI7DYKRBP)
* **Escrow Contract Template (WASM Hash)**: `df39861bdd6a838826acb7fc9d965563ab166d5d15cd83cc9a8671448e0696ee`
  * [Stellar Expert Upload Verification](https://stellar.expert/explorer/public/tx/9baca5926e5cafca09e4e400f08add08d202ed39affebf59f7b4985f9adbfa65)

---

### 💻 Command Line Interface (CLI)
* **Version Bump:** Upgraded version to `0.5.0` in `cli/mycelium_cli/main.py`.
* **Shorthand Network Flags:**
  * Added `--testnet` / `-t` flag to force Stellar Testnet (the default CLI network).
  * Added `--mainnet` / `-m` flag to force Stellar Mainnet (Public network).
  * Added shorthand `-n` option as an alias to `--network`.
* **Subcommand Upgrades:** Applied identical flag structures (`-t`, `-m`, `-n`) and `resolve_network` helper validation to all CLI subcommand modules: `jobs`, `deal`, `memory`, and `verifier`.

---

### 📦 Python SDK
* **Per-Network Constants:** Refactored `constants.py` to replace single-value scalars with a `CONTRACT_ADDRESSES` dictionary mapping both testnet and mainnet contract IDs.
* **Smart Resolver Helper:** Introduced `contract_address(name, network)` which resolves network names and prevents errors if mainnet tries to hit un-deployed placeholder addresses.
* **Network-Aware Clients:** Refactored `HiveClient` and `MemoryAnchorClient` constructors to dynamically resolve contract IDs based on context network type (`self.context.network_type`).
* **Multi-Network Business Model / Protocol Fees:**
  * Defined network-specific default fee collectors (`FEE_COLLECTORS` mapping).
  * Configured `myceliumtestnet` (`GCKYLSBT7VE5XW326LCGV72TZRYDX5WIX3TKCE74GU4WBTVSVUDBPAYR`) as the payee for testnet conditional escrows.
  * Configured `myceliummainnet` (`GCT7GPSGA4OQXCN6KYUVDCZY2P4D4QHA5GCPC72XYEN3RRF36NR6D2XX`) as the payee for mainnet business escrows.
  * Introduced `protocol_fee_collector(network)` to resolve collector addresses dynamically, allowing env-var overrides via `MYCELIUM_FEE_COLLECTOR`.

---

### 🎨 Web IDE Frontend
* **Shared Network Config:** Built `network-config.ts` mapping RPC servers, Horizon API endpoints, Stellar Expert paths, and contract addresses for both mainnet and testnet.
* **Freighter Wallet Integration:** Updated the Bounty Board, Swarm Directory, and Monaco Playground to auto-detect the connected wallet network ("PUBLIC" -> `mainnet`, "TESTNET" -> `testnet`) and load the correct contracts, RPC simulation pipelines, and Horizon transaction links.
* **Bounty Payout Split:** Refactored `bounty/page.tsx`'s escrow instantiation logic to route the protocol fee to the corresponding network collector (`myceliummainnet` or `myceliumtestnet`).
* **UI enhancements:** Added a wallet connection status/details component to the registry nodes list. Relocated the Search/Resolve Box to the Registry Address Card for improved layout.

---

### 🔍 Firestore Indexer Worker
* **Network-Aware Event Scan:** Replaced the hardcoded testnet constants with `contract_address(name, network)` resolution in `build_default_worker`, allowing the Firestore worker daemon to catch up, scan, and index mainnet registry events using:
  ```bash
  python3 -m indexer.worker --network mainnet
  ```
