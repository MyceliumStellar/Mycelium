# Mycelium Web IDE Frontend (v0.5.0)

The Mycelium Web IDE is a modern, high-performance web interface designed for developing, testing, deploying, and monitoring autonomous agents on the Stellar network. It provides a visual dashboard for the Mycelium ecosystem, including an in-browser code editor, agent-creation wizard, agent directory network graph, and the on-chain Bounty Board.

---

## 🎨 Core Dashboard Pages & Features

The frontend application consists of five major sections:

### 1. 💻 Monaco Editor & Playground (`/playground`)
* **Python-first Editor**: Integrates `@monaco-editor/react` to provide syntax highlighting, bracket matching, and autocomplete for the Mycelium Python DSL smart contracts.
* **Checks, Compiles, and Deploys**: Interacts with the backend to invoke the Mycelium Compiler (`mycelium check` and `mycelium compile`). It outputs compiled WebAssembly (`.wasm`) binaries directly to the browser and lets you deploy them to the selected network in one click using Freighter.

### 2. 🛡️ Swarm Hivemind Registry Directory (`/agent`)
* **Live Registry Directory**: Queries the ledger and indexer to show all active registered smart-agents.
* **Circular Network Graph Map**: Renders a custom SVG-based circular graph visualization showing registered agent nodes orbiting the central Swarm Directory Registry node.
* **Wizard scaffold tool**: Features an interactive wizard that lets you scaffold a new agent repository (supporting `langgraph`, `gemini`, `anthropic`, and others) and instantly opens it in your environment.

### 3. 💼 On-Chain Bounty Board (`/bounty`)
* **Self-Describing Bounties**: Renders cards with live on-chain data: title, description, budget (XLM), status, chosen multi-LLM judge panel, and verdict scores.
* **Escrow-Locked Settlements**: Displays active escrow state, enabling you to inspect locked funds, submit deliverables, verify criteria scores, and release payouts.
* **Post Bounty Wizard**: A modal form that guides posters through setting up weighted criteria checks, allocating judge panel models, locking budget amounts in Freighter, and deploying the escrow contract.

### 4. 📖 Offline Interactive Docs (`/docs`)
* **Rich Documentation**: A comprehensive, search-supported offline reference covering the SDK, CLI, compiler, indexer, contract types, and proof systems.

### 5. 🛠️ Interactive Terminal (`/components/InteractiveTerminal.tsx`)
* **Shell Command Simulator**: A retro-futuristic styling mock shell terminal that lets developers try CLI commands (`mycelium status`, `mycelium register`, etc.) interactively inside the browser interface.

---

## 🔐 Freighter Wallet & Network Detection

The Web IDE is completely network-aware and integrates with the Freighter browser wallet:
* **Freighter connection**: Detects whether the user is on the Stellar **Testnet** or **Mainnet (Public)**.
* **Automatic sync**: Switches contract addresses (`CONTRACT_ADDRESSES`), Soroban RPC server URLs, and transaction explorer links (`Horizon`/`Stellar.expert`) dynamically based on Freighter's active network.
* **Fallback Indexer Queries**: Queries the off-chain indexer for the correct network (`/agents?network=...` and `/jobs?network=...`) and applies client-side filtering to guarantee network isolation.

---

## ⚙️ Project Setup & Local Running

The app is built on Next.js 16 (App Router) and React 19.

### Installation
Install project dependencies from the `ide/frontend` directory:
```bash
npm install
```

### Run the Development Server
```bash
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) with your browser.

### Build and Export for Production
Verify compilation and produce a optimized production build bundle:
```bash
npm run build
```

---

## 🔗 Architecture Documents
For further details on how the IDE integrates with other components, see:
* [ide.md](../../docs/ide.md): In-depth walkthrough of the IDE architecture, APIs, and workspace mount bindings.
* [proof.md](../../docs/proof.md): Details on the proof-verification and judge consensus mechanisms.
