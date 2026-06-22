"use client";

import React, { useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  Terminal,
  Code,
  Cpu,
  ShoppingBag,
  Layers,
  ArrowRight,
  Copy,
  Check,
  ChevronRight,
  BookOpen,
  HelpCircle,
  ExternalLink
} from "lucide-react";

// Documentation Content Schema
interface DocSection {
  title: string;
  subtitle: string;
  description: string;
  icon: React.ReactNode;
  color: string;
  overview: string;
  installation?: {
    command: string;
    description: string;
  };
  quickStartCode?: {
    filename: string;
    code: string;
    language: string;
  };
  details: {
    sectionTitle: string;
    content: string | React.JSX.Element;
  }[];
}

const CopyButton = ({ text }: { text: string }) => {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button
      onClick={handleCopy}
      style={{
        background: "rgba(255, 255, 255, 0.05)",
        border: "1px solid rgba(255, 255, 255, 0.08)",
        borderRadius: "4px",
        color: copied ? "#0f9f78" : "rgba(255, 255, 255, 0.5)",
        padding: "4px 8px",
        fontSize: "0.72rem",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        gap: "4px",
        transition: "all 0.2s"
      }}
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
};

export default function DocsPage() {
  const [isRevealed, setIsRevealed] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);

  const docSections: Record<string, DocSection> = {
    cli: {
      title: "Mycelium CLI",
      subtitle: "Command Line Interface for Swarm Orchestration",
      description: "Developer toolbelt for scaffolding projects, compiling Python smart contracts to WASM, and managing Stellar network deployments.",
      icon: <Terminal size={20} />,
      color: "#0096c7", // Cyan
      overview: "The Mycelium CLI bridges the local development workspace with the Stellar Soroban network. It automates the environment configurations, key management, project template scaffolding, AST compile checking, WebAssembly building, and registration on the decentralised on-chain Hivemind registry.",
      installation: {
        command: "pip install mycelium-cli",
        description: "Requires Python >= 3.9 and Cargo/Rust installed locally for compilation pipelines."
      },
      quickStartCode: {
        filename: "cli_workflow.sh",
        language: "bash",
        code: `# Initialize a new agent workspace project
mycelium init my-swarm-agent
cd my-swarm-agent

# Build & compile the Python contract to WebAssembly
mycelium compile

# Setup cryptographic keypair on Stellar Testnet
mycelium keys generate dev-wallet

# Deploy contract to Stellar Testnet ledger
mycelium deploy --network testnet --key dev-wallet

# Register agent node in the central registry
mycelium register --name my_swarm_agent --endpoint https://agent.mycelium.org`
      },
      details: [
        {
          sectionTitle: "CLI Command Reference Table",
          content: (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem", color: "rgba(255,255,255,0.7)" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.1)", textAlign: "left" }}>
                    <th style={{ padding: "8px 0", color: "#ffffff", fontWeight: "600" }}>Command</th>
                    <th style={{ padding: "8px 12px", color: "#ffffff", fontWeight: "600" }}>Description</th>
                    <th style={{ padding: "8px 0", color: "#ffffff", fontWeight: "600" }}>Key Arguments</th>
                  </tr>
                </thead>
                <tbody>
                  <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                    <td style={{ padding: "10px 0", fontFamily: "var(--font-mono)", color: "var(--accent-cyan)" }}>init</td>
                    <td style={{ padding: "10px 12px" }}>Scaffolds a new template-based agent workspace with boilerplate code.</td>
                    <td style={{ padding: "10px 0", fontFamily: "var(--font-mono)" }}>&lt;project_name&gt;</td>
                  </tr>
                  <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                    <td style={{ padding: "10px 0", fontFamily: "var(--font-mono)", color: "var(--accent-cyan)" }}>compile</td>
                    <td style={{ padding: "10px 12px" }}>Transpiles local Python smart contracts into Rust & builds WASM binaries.</td>
                    <td style={{ padding: "10px 0", fontFamily: "var(--font-mono)" }}>--release</td>
                  </tr>
                  <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                    <td style={{ padding: "10px 0", fontFamily: "var(--font-mono)", color: "var(--accent-cyan)" }}>deploy</td>
                    <td style={{ padding: "10px 12px" }}>Simulates transactions, uploads WASM binaries, and deploys contract to ledger.</td>
                    <td style={{ padding: "10px 0", fontFamily: "var(--font-mono)" }}>--network, --key</td>
                  </tr>
                  <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                    <td style={{ padding: "10px 0", fontFamily: "var(--font-mono)", color: "var(--accent-cyan)" }}>register</td>
                    <td style={{ padding: "10px 12px" }}>Invokes the Hive Registry contract on-chain to directory-bind name & URI.</td>
                    <td style={{ padding: "10px 0", fontFamily: "var(--font-mono)" }}>--name, --endpoint</td>
                  </tr>
                </tbody>
              </table>
            </div>
          )
        },
        {
          sectionTitle: "Environment Setup Variables",
          content: "The CLI detects configurations from the local `.env` or system variables. Make sure your environment contains: STELLAR_RPC_URL (e.g. https://soroban-testnet.stellar.org) and STELLAR_PASSPHRASE for the target testnet, to avoid manually specifying network overrides on each command invocation."
        }
      ]
    },
    sdk: {
      title: "Mycelium SDK",
      subtitle: "Software Development Kit for Agent Programming",
      description: "Libraries for Python and JavaScript/TypeScript to dynamically resolve agents, orchestrate payments, and interact with smart contracts.",
      icon: <Code size={20} />,
      color: "#8b5cf6", // Purple
      overview: "The Mycelium SDK simplifies building web applications, autonomous clients, or microservices that need to communicate with the Mycelium ecosystem. It wraps Stellar's Soroban SDK to query on-chain variables, decrypt payloads, communicate with agent HTTP endpoints, and perform micro-payments programmatically.",
      installation: {
        command: "pip install mycelium-sdk\n# or\nnpm install @mycelium-stellar/sdk",
        description: "Available on PyPI and npm for seamless integration with frontend and backend projects."
      },
      quickStartCode: {
        filename: "resolve_and_interact.py",
        language: "python",
        code: `import os
from mycelium import HiveClient, Symbol

# Initialize SDK Hive Client connected to Testnet RPC
client = HiveClient(
    rpc_url="https://soroban-testnet.stellar.org",
    network_passphrase="Test SDF Network ; September 2015"
)

# Registry contract address on Testnet
REGISTRY_ADDRESS = "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC"

# Resolve agent details from blockchain registry
agent = client.resolve_agent(REGISTRY_ADDRESS, "market_oracle_node")

print(f"Agent Wallet Address: {agent['agent_id']}")
print(f"Operational URL: {agent['endpoint']}")
print(f"Capability Hash: {agent['capability_hash'].hex()}")`
      },
      details: [
        {
          sectionTitle: "Python SDK API Reference",
          content: (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px", fontSize: "0.85rem" }}>
              <div>
                <strong style={{ color: "#ffffff" }}>HiveClient.resolve_agent(registry_contract_id: str, name: str) -&gt; dict</strong>
                <p style={{ color: "rgba(255,255,255,0.6)" }}>Queries the Stellar Soroban blockchain registry to get agent details (endpoint, capability, public key).</p>
              </div>
              <div>
                <strong style={{ color: "#ffffff" }}>HiveClient.register_agent(registry_contract_id: str, name: str, agent_id: str, capability: bytes, endpoint: str, signer_key: str) -&gt; str</strong>
                <p style={{ color: "rgba(255,255,255,0.6)" }}>Performs an on-chain invocation to register an agent name. Returns the Stellar transaction hash.</p>
              </div>
            </div>
          )
        },
        {
          sectionTitle: "JavaScript SDK Integration",
          content: "For frontend applications integration (e.g. matching Freighter wallet connections), import `HiveClient` from `@mycelium-stellar/sdk`. It supports Web3 transaction signing out-of-the-box using the Freighter browser extension API, allowing client-side contracts deployments and registration updates."
        }
      ]
    },
    compiler: {
      title: "Mycelium Compiler",
      subtitle: "Python to Soroban WASM Transpiler",
      description: "Secure compiler translating Python AST structure to type-safe Rust and WebAssembly optimized for the Soroban virtual machine.",
      icon: <Cpu size={20} />,
      color: "#0f9f78", // Green
      overview: "The Mycelium Compiler enables Python developers to write high-performance smart contracts without needing to learn Rust. It validates typing rules, checks AST structures to prevent security loopholes (such as dynamic array expansions or arbitrary libraries loads), and produces optimized WASM binaries compatible with Stellar's Virtual Machine.",
      quickStartCode: {
        filename: "agent_contract.py",
        language: "python",
        code: `from mycelium import contract, state, Symbol, i128

@contract
class MarketOracleAgent:
    provider: Symbol
    price_feed: i128

    @state.instance
    def initialize(self, owner: Symbol, initial_price: i128):
        self.provider = owner
        self.price_feed = initial_price

    @state.instance
    def update_price(self, caller: Symbol, new_price: i128) -> bool:
        if caller != self.provider:
            return False
        self.price_feed = new_price
        return True

    @state.instance
    def get_price(self) -> i128:
        return self.price_feed`
      },
      details: [
        {
          sectionTitle: "Supported Python Types & Decorators",
          content: (
            <div style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.7)" }}>
              <ul style={{ listStyleType: "square", paddingLeft: "20px", display: "flex", flexDirection: "column", gap: "8px" }}>
                <li><strong style={{ color: "#ffffff" }}>@contract</strong>: Decorates the primary class that holds contract storage and methods.</li>
                <li><strong style={{ color: "#ffffff" }}>@state.instance</strong>: Stores variables bound directly to the contract instance (cheaper gas costs for active updates).</li>
                <li><strong style={{ color: "#ffffff" }}>@state.persistent</strong>: Stores variables that persist across contract lifetime upgrades.</li>
                <li><strong style={{ color: "#ffffff" }}>Symbol, i128, u256, Map, Bytes, Address</strong>: Mapped directly to Soroban virtual types.</li>
              </ul>
            </div>
          )
        },
        {
          sectionTitle: "Compiler Security Safeguards",
          content: "The compiler restricts dynamic behavior (such as `eval()`, variable shadowing, or generic `import` libraries) to ensure that the translated WebAssembly code is fully deterministic and free from buffer overflows or arbitrary re-entrancy bugs."
        }
      ]
    },
    commerce: {
      title: "Mycelium A2A Commerce",
      subtitle: "Agent-to-Agent Micro-Payment & Settlement Engine",
      description: "Micro-transactions settlement framework for agents to trade data, buy services, and escrow funds programmatically on Stellar.",
      icon: <ShoppingBag size={20} />,
      color: "#ffcc00", // Yellow
      overview: "Agent-to-Agent (A2A) Commerce facilitates autonomous economic interactions. Through standardized escrow contracts, micro-payment API wrappers, and cryptographic validation mechanisms, agents can buy computing capacity, rent oracle inputs, or purchase datasets. Settle instantly using XLM or any custom token.",
      quickStartCode: {
        filename: "payment_escrow.py",
        language: "python",
        code: `from mycelium import contract, state, Address, i128

@contract
class EscrowCommerce:
    buyer: Address
    seller: Address
    amount: i128
    is_released: bool

    @state.instance
    def initialize(self, buyer: Address, seller: Address, amount: i128):
        self.buyer = buyer
        self.seller = seller
        self.amount = amount
        self.is_released = False

    @state.instance
    def release_escrow(self, caller: Address):
        # Escrow can only be completed by the designated buyer agent
        if caller == self.buyer and not self.is_released:
            # Invokes payment interface to forward locked tokens to the seller
            self.send_payment(self.seller, self.amount)
            self.is_released = True`
      },
      details: [
        {
          sectionTitle: "Use Cases",
          content: (
            <div style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.7)" }}>
              <p style={{ marginBottom: "10px" }}><strong style={{ color: "#ffffff" }}>Data Marketplace</strong>: Oracle agents vending real-world API data feeds to other processing agents on-demand, charging micro-XLM per query.</p>
              <p style={{ marginBottom: "10px" }}><strong style={{ color: "#ffffff" }}>Compute Orchestration</strong>: Agents delegating heavy processing algorithms to third-party GPU clusters, escrowing funds until proofs of computation are presented on-chain.</p>
              <p><strong style={{ color: "#ffffff" }}>SLA Penalisation</strong>: Escrows which automatically penalize or refund agents if latency or availability metrics register beneath threshold levels.</p>
            </div>
          )
        }
      ]
    },
    registry: {
      title: "Mycelium Hive Registry",
      subtitle: "On-Chain Registry & Swarm Directory",
      description: "Decentralized registry deployed on Stellar Testnet mapping agent identities to operational metadata, endpoints, and credentials.",
      icon: <Layers size={20} />,
      color: "#ff3b30", // Red / Rose
      overview: "The Mycelium Hive Registry acts as the on-chain DNS for decentralized agent swarm networks. It operates dynamically on Stellar Testnet, resolving agent identities from names, returning endpoints, verifying public keys, checking reputation parameters, and emitting events on new registries.",
      details: [
        {
          sectionTitle: "Testnet Contract Information",
          content: (
            <div style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.7)" }}>
              <p style={{ marginBottom: "10px" }}>The Hive Registry is deployed on Stellar Testnet at the following contract hash address:</p>
              <div style={{
                fontFamily: "var(--font-mono)",
                background: "rgba(255,255,255,0.02)",
                border: "1px solid rgba(255,255,255,0.08)",
                padding: "10px 14px",
                borderRadius: "6px",
                color: "var(--accent-yellow)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: "15px"
              }}>
                <span>CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC</span>
                <CopyButton text="CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC" />
              </div>
              <p>Applications and CLI agents interact directly with this address to fetch node records in real-time, bypassing centralized database states entirely.</p>
            </div>
          )
        },
        {
          sectionTitle: "On-Chain Registry Events",
          content: (
            <div style={{ fontSize: "0.85rem" }}>
              <p style={{ marginBottom: "8px", color: "rgba(255,255,255,0.6)" }}>When an agent registry or update is made, the registry emits a Soroban event that the SDK registers: </p>
              <div style={{
                position: "relative",
                background: "rgba(0,0,0,0.4)",
                border: "1px solid rgba(255,255,255,0.05)",
                borderRadius: "6px",
                padding: "12px",
                fontFamily: "var(--font-mono)",
                fontSize: "0.8rem",
                color: "rgba(255,255,255,0.85)",
                lineHeight: "1.4"
              }}>
                <div style={{ position: "absolute", top: "10px", right: "10px" }}>
                  <CopyButton text={`# Topic: ["agent_registered", agent_name]\n# Data: [agent_id_bytes, capability_hash, uri_bytes]`} />
                </div>
                <div>Topic: <span style={{ color: "var(--accent-cyan)" }}>["agent_registered", Symbol("market_oracle")]</span></div>
                <div>Data: <span style={{ color: "var(--accent-green)" }}>[Bytes(ID), Bytes(CapabilityHash), Bytes(URI)]</span></div>
              </div>
            </div>
          )
        }
      ]
    }
  };

  return (
    <div style={{
      position: "relative",
      backgroundColor: "var(--background)",
      color: "var(--foreground)",
      minHeight: "100vh",
      width: "100%",
      fontFamily: "var(--font-sans), sans-serif",
      overflowX: "hidden",
      paddingBottom: "80px"
    }}>
      {/* Background Grid */}
      <div className="premium-grid" style={{
        position: "fixed",
        top: 0, left: 0, right: 0, bottom: 0,
        pointerEvents: "none",
        zIndex: 0
      }} />

      {/* Decorative Orbs */}
      <div className="glow-orb-cyan" style={{
        position: "absolute",
        top: "-100px",
        left: "20%",
        width: "600px",
        height: "400px",
        pointerEvents: "none",
        zIndex: 1
      }} />
      <div className="glow-orb-purple" style={{
        position: "absolute",
        bottom: "10%",
        right: "10%",
        width: "500px",
        height: "400px",
        pointerEvents: "none",
        zIndex: 1
      }} />

      {/* Header */}
      <header style={{
        position: "sticky",
        top: 0,
        zIndex: 100,
        background: "rgba(4, 4, 5, 0.9)",
        backdropFilter: "blur(16px)",
        borderBottom: "1px solid rgba(255, 255, 255, 0.06)"
      }}>
        <div style={{
          maxWidth: "1200px",
          margin: "0 auto",
          padding: "15px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between"
        }}>
          <Link href="/" style={{ display: "flex", alignItems: "center", color: "var(--foreground)" }}>
            <span className="font-display" style={{ fontSize: "1.2rem", fontWeight: 800, letterSpacing: "-0.04em" }}>
              Mycelium
            </span>
          </Link>

          <nav style={{ display: "flex", gap: "28px" }}>
            <Link href="/#features"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >features</Link>
            <Link href="/#architecture"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >architecture</Link>
            <Link href="/agent"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >agents</Link>
            <Link href="/docs"
              style={{ fontSize: "0.78rem", color: "#ffffff", fontWeight: 500 }}
            >docs</Link>
          </nav>

          <Link href="/playground" className="premium-button-primary" style={{
            padding: "7px 16px",
            fontSize: "0.76rem",
            borderRadius: "6px"
          }}>
            Launch Playground
          </Link>
        </div>
      </header>

      {/* Main Container */}
      <main style={{
        maxWidth: "1200px",
        margin: "0 auto",
        padding: "48px 24px 0",
        position: "relative",
        zIndex: 10
      }}>
        {/* Page Titles */}
        <div style={{ textAlign: "center", marginBottom: "48px" }}>
          <span style={{
            fontSize: "0.68rem",
            fontFamily: "var(--font-mono)",
            color: "var(--accent-purple)",
            textTransform: "uppercase",
            letterSpacing: "3px",
            fontWeight: "bold",
            display: "block",
            marginBottom: "12px"
          }}>
            Developer Documentation
          </span>
          <h1 className="font-display" style={{
            fontSize: "clamp(2rem, 5vw, 3rem)",
            fontWeight: 800,
            color: "#ffffff",
            letterSpacing: "-0.045em",
            marginBottom: "16px"
          }}>
            Technical Resource Hub
          </h1>
          <p style={{
            fontSize: "0.95rem",
            color: "rgba(255, 255, 255, 0.55)",
            maxWidth: "600px",
            margin: "0 auto",
            fontWeight: 300,
            lineHeight: "1.6"
          }}>
            Deploy smart-agents to Stellar, build micro-transaction engines, and manage swarms locally.
          </p>
        </div>

        {/* GLOBE SECTION */}
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: "60px"
        }}>
          <motion.div
            style={{
              position: "relative",
              width: "360px",
              height: "360px",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: "50%",
              boxShadow: "0 0 50px rgba(0, 150, 199, 0.15)"
            }}
            onClick={() => {
              setIsRevealed(true);
              if (!selectedDocId) {
                setSelectedDocId("cli");
              }
            }}
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.98 }}
            transition={{ type: "spring", stiffness: 300, damping: 20 }}
          >
            {/* Holographic Glowing Rings */}
            <div style={{
              position: "absolute",
              width: "115%",
              height: "115%",
              borderRadius: "50%",
              background: "radial-gradient(circle, rgba(0, 150, 199, 0.08) 0%, transparent 70%)",
              filter: "blur(15px)",
              pointerEvents: "none"
            }} />

            {/* Rotating SVG Globe */}
            <svg
              viewBox="0 0 100 100"
              style={{
                width: "100%",
                height: "100%",
                zIndex: 2
              }}
            >
              {/* Outer boundary ring */}
              <circle
                cx="50"
                cy="50"
                r="46"
                fill="none"
                stroke="rgba(0, 150, 199, 0.2)"
                strokeWidth="0.5"
                strokeDasharray="3 3"
              />
              <circle
                cx="50"
                cy="50"
                r="43"
                fill="rgba(4, 4, 5, 0.7)"
                stroke="rgba(0, 150, 199, 0.7)"
                strokeWidth="1.2"
              />

              {/* Longitude meridians (horizontal scale animations simulated in CSS styles below) */}
              <g stroke="rgba(0, 150, 199, 0.3)" strokeWidth="0.4" fill="none">
                <path d="M 50 7 A 43 43 0 0 0 50 93" className="meridian-line meridian-a" />
                <path d="M 50 7 A 28 43 0 0 0 50 93" className="meridian-line meridian-b" />
                <path d="M 50 7 A 14 43 0 0 0 50 93" className="meridian-line meridian-c" />
                <path d="M 50 7 L 50 93" stroke="rgba(0, 150, 199, 0.5)" strokeWidth="0.8" />
                <path d="M 50 7 A 14 43 0 0 1 50 93" className="meridian-line meridian-c" />
                <path d="M 50 7 A 28 43 0 0 1 50 93" className="meridian-line meridian-b" />
                <path d="M 50 7 A 43 43 0 0 1 50 93" className="meridian-line meridian-a" />
              </g>

              {/* Latitude parallel lines */}
              <g stroke="rgba(0, 150, 199, 0.2)" strokeWidth="0.4" fill="none">
                <line x1="18" y1="21" x2="82" y2="21" />
                <line x1="11" y1="35" x2="89" y2="35" />
                <line x1="7" y1="50" x2="93" y2="50" stroke="rgba(0, 150, 199, 0.4)" strokeWidth="0.8" />
                <line x1="11" y1="65" x2="89" y2="65" />
                <line x1="18" y1="79" x2="82" y2="79" />
              </g>
            </svg>

            {/* Central Plaque text */}
            <div style={{
              position: "absolute",
              width: "72%",
              height: "72%",
              borderRadius: "50%",
              background: "rgba(8, 8, 10, 0.9)",
              border: "1px solid rgba(0, 150, 199, 0.35)",
              backdropFilter: "blur(12px)",
              boxShadow: "0 0 20px rgba(0, 150, 199, 0.15)",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              padding: "16px",
              textAlign: "center",
              zIndex: 3,
              pointerEvents: "none"
            }}>
              <span className="font-display" style={{
                fontSize: "1.1rem",
                fontWeight: 800,
                color: "#ffffff",
                letterSpacing: "1px",
                textTransform: "uppercase"
              }}>
                Mycelium
              </span>
              <p style={{
                fontSize: "0.68rem",
                color: "rgba(255,255,255,0.65)",
                lineHeight: "1.3",
                margin: "6px 0 10px",
                maxWidth: "180px"
              }}>
                an agentic infrastructure on stellar
              </p>
              <div style={{
                fontSize: "0.58rem",
                background: "rgba(139, 92, 246, 0.15)",
                border: "1px solid rgba(139, 92, 246, 0.3)",
                color: "var(--accent-purple)",
                padding: "2px 8px",
                borderRadius: "20px",
                fontWeight: "bold",
                letterSpacing: "0.5px"
              }}>
                Live in Testnet (Soon In mainnet)
              </div>
            </div>

            {/* Global Keyframes Animation */}
            <style jsx>{`
              @keyframes rotateX {
                0% { transform: scaleX(1); opacity: 0.3; }
                50% { transform: scaleX(0.1); opacity: 0.7; }
                100% { transform: scaleX(1); opacity: 0.3; }
              }
              .meridian-a {
                animation: rotateX 10s linear infinite;
                transform-origin: center;
              }
              .meridian-b {
                animation: rotateX 7s linear infinite;
                transform-origin: center;
              }
              .meridian-c {
                animation: rotateX 4s linear infinite;
                transform-origin: center;
              }
            `}</style>
          </motion.div>

          {/* Interactive Cue */}
          {!isRevealed && (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: [0.4, 1, 0.4], y: 0 }}
              transition={{ repeat: Infinity, duration: 2.5 }}
              style={{
                marginTop: "20px",
                fontSize: "0.85rem",
                fontFamily: "var(--font-mono)",
                color: "var(--accent-cyan)",
                letterSpacing: "1px",
                display: "flex",
                alignItems: "center",
                gap: "8px"
              }}
            >
              <span>CLICK THE GLOBE TO LAUNCH INFRASTRUCTURE MODULES</span>
              <ArrowRight size={14} />
            </motion.div>
          )}
        </div>

        {/* SATELITES / BENTO OPTIONS GRID */}
        <AnimatePresence>
          {isRevealed && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.5 }}
              style={{
                width: "100%",
                maxWidth: "1000px",
                margin: "0 auto 48px"
              }}
            >
              <div style={{
                textAlign: "center",
                marginBottom: "25px",
                fontFamily: "var(--font-mono)",
                fontSize: "0.8rem",
                color: "rgba(255,255,255,0.4)"
              }}>
                SELECT MODULE FOR DETAILED DOCUMENTATION
              </div>

              {/* The 5 satelite options bento grid */}
              <div style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                gap: "14px"
              }}>
                {[
                  { id: "cli", title: "Mycelium CLI", subtitle: "Command Line", icon: <Terminal size={18} />, color: docSections.cli.color },
                  { id: "sdk", title: "Mycelium SDK", subtitle: "Developer Libs", icon: <Code size={18} />, color: docSections.sdk.color },
                  { id: "compiler", title: "Mycelium Compiler", subtitle: "WASM Transpiler", icon: <Cpu size={18} />, color: docSections.compiler.color },
                  { id: "commerce", title: "A2A Commerce", subtitle: "Value Settlements", icon: <ShoppingBag size={18} />, color: docSections.commerce.color },
                  { id: "registry", title: "Hive Registry", subtitle: "Swarm Directory", icon: <Layers size={18} />, color: docSections.registry.color }
                ].map(item => {
                  const isSelected = selectedDocId === item.id;
                  return (
                    <button
                      key={item.id}
                      onClick={() => {
                        setSelectedDocId(item.id);
                        // Smooth scroll to the doc container
                        document.getElementById("doc-display-container")?.scrollIntoView({ behavior: "smooth" });
                      }}
                      style={{
                        background: isSelected ? "rgba(255, 255, 255, 0.04)" : "rgba(255, 255, 255, 0.01)",
                        border: `1px solid ${isSelected ? item.color : "rgba(255,255,255,0.06)"}`,
                        borderRadius: "8px",
                        padding: "16px",
                        cursor: "pointer",
                        color: "#ffffff",
                        textAlign: "left",
                        display: "flex",
                        flexDirection: "column",
                        gap: "10px",
                        position: "relative",
                        overflow: "hidden",
                        transition: "all 0.25s ease",
                        boxShadow: isSelected ? `0 0 20px ${item.color}15` : "none"
                      }}
                      onMouseEnter={e => {
                        if (!isSelected) e.currentTarget.style.borderColor = "rgba(255,255,255,0.18)";
                      }}
                      onMouseLeave={e => {
                        if (!isSelected) e.currentTarget.style.borderColor = "rgba(255,255,255,0.06)";
                      }}
                    >
                      {/* Active indicator bar */}
                      {isSelected && (
                        <div style={{
                          position: "absolute",
                          left: 0, top: 0, bottom: 0,
                          width: "3px",
                          background: item.color
                        }} />
                      )}

                      <div style={{
                        width: "32px",
                        height: "32px",
                        borderRadius: "6px",
                        background: `${item.color}18`,
                        color: item.color,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center"
                      }}>
                        {item.icon}
                      </div>

                      <div>
                        <div style={{ fontSize: "0.95rem", fontWeight: "600", color: "#ffffff" }}>{item.title}</div>
                        <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.45)", marginTop: "2px" }}>{item.subtitle}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* DETAILED DOCUMENTATION DISPLAY */}
        <AnimatePresence>
          {isRevealed && selectedDocId && docSections[selectedDocId] && (
            <motion.div
              id="doc-display-container"
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 30 }}
              transition={{ duration: 0.4 }}
              style={{
                width: "100%",
                maxWidth: "1000px",
                margin: "0 auto"
              }}
            >
              {/* Documentation Body Panel */}
              <div className="premium-card" style={{
                borderRadius: "12px",
                padding: "36px",
                borderTop: `2.5px solid ${docSections[selectedDocId].color}`,
                background: "rgba(10, 10, 12, 0.4)",
                boxShadow: "0 20px 40px -20px rgba(0,0,0,0.8)"
              }}>
                {/* Header section */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "20px", marginBottom: "30px", borderBottom: "1px solid rgba(255,255,255,0.06)", paddingBottom: "25px" }}>
                  <div>
                    <span style={{ fontSize: "0.72rem", fontFamily: "var(--font-mono)", color: docSections[selectedDocId].color, textTransform: "uppercase", letterSpacing: "1.5px" }}>
                      MODULE DOCUMENTATION
                    </span>
                    <h2 className="font-display" style={{ fontSize: "1.85rem", fontWeight: "800", color: "#ffffff", marginTop: "4px" }}>
                      {docSections[selectedDocId].title}
                    </h2>
                    <p style={{ fontSize: "0.95rem", color: "rgba(255,255,255,0.6)", marginTop: "6px" }}>
                      {docSections[selectedDocId].subtitle}
                    </p>
                  </div>
                  <div style={{
                    fontSize: "0.78rem",
                    padding: "4px 10px",
                    borderRadius: "4px",
                    border: `1px solid ${docSections[selectedDocId].color}30`,
                    background: `${docSections[selectedDocId].color}08`,
                    color: docSections[selectedDocId].color,
                    fontFamily: "var(--font-mono)"
                  }}>
                    v0.1.0-alpha
                  </div>
                </div>

                {/* Body Content */}
                <div style={{ display: "flex", flexDirection: "column", gap: "28px" }}>
                  {/* Overview */}
                  <div>
                    <h4 style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "1px", color: "rgba(255,255,255,0.45)", marginBottom: "8px", fontFamily: "var(--font-mono)" }}>
                      Overview
                    </h4>
                    <p style={{ fontSize: "0.95rem", lineHeight: "1.6", color: "rgba(255,255,255,0.8)", fontWeight: 300 }}>
                      {docSections[selectedDocId].overview}
                    </p>
                  </div>

                  {/* Installation */}
                  {docSections[selectedDocId].installation && (
                    <div>
                      <h4 style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "1px", color: "rgba(255,255,255,0.45)", marginBottom: "8px", fontFamily: "var(--font-mono)" }}>
                        Installation
                      </h4>
                      <p style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.5)", marginBottom: "10px" }}>
                        {docSections[selectedDocId].installation?.description}
                      </p>
                      <div style={{
                        position: "relative",
                        background: "rgba(0,0,0,0.6)",
                        border: "1px solid rgba(255,255,255,0.06)",
                        borderRadius: "6px",
                        padding: "14px",
                        fontFamily: "var(--font-mono)",
                        fontSize: "0.85rem",
                        color: "#0f9f78"
                      }}>
                        <div style={{ position: "absolute", top: "10px", right: "10px", zIndex: 10 }}>
                          <CopyButton text={docSections[selectedDocId].installation?.command || ""} />
                        </div>
                        <pre style={{ margin: 0 }}>$ {docSections[selectedDocId].installation?.command}</pre>
                      </div>
                    </div>
                  )}

                  {/* Quickstart Code Block */}
                  {docSections[selectedDocId].quickStartCode && (
                    <div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                        <h4 style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "1px", color: "rgba(255,255,255,0.45)", fontFamily: "var(--font-mono)" }}>
                          Quick Start Template
                        </h4>
                        <span style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.45)", fontFamily: "var(--font-mono)" }}>
                          {docSections[selectedDocId].quickStartCode?.filename}
                        </span>
                      </div>
                      <div style={{
                        position: "relative",
                        background: "rgba(0,0,0,0.65)",
                        border: "1px solid rgba(255,255,255,0.06)",
                        borderRadius: "6px",
                        padding: "16px",
                        fontFamily: "var(--font-mono)",
                        fontSize: "0.82rem",
                        color: "rgba(255,255,255,0.85)",
                        lineHeight: "1.5",
                        overflowX: "auto"
                      }}>
                        <div style={{ position: "absolute", top: "12px", right: "12px", zIndex: 10 }}>
                          <CopyButton text={docSections[selectedDocId].quickStartCode?.code || ""} />
                        </div>
                        <pre style={{ margin: 0 }}>{docSections[selectedDocId].quickStartCode?.code}</pre>
                      </div>
                    </div>
                  )}

                  {/* Additional detailed sections */}
                  {docSections[selectedDocId].details.map((section, idx) => (
                    <div key={idx} style={{ borderTop: "1px solid rgba(255,255,255,0.05)", paddingTop: "20px" }}>
                      <h4 style={{ fontSize: "0.85rem", textTransform: "uppercase", letterSpacing: "1px", color: "rgba(255,255,255,0.45)", marginBottom: "10px", fontFamily: "var(--font-mono)" }}>
                        {section.sectionTitle}
                      </h4>
                      {typeof section.content === "string" ? (
                        <p style={{ fontSize: "0.92rem", lineHeight: "1.6", color: "rgba(255,255,255,0.7)", fontWeight: 300 }}>
                          {section.content}
                        </p>
                      ) : (
                        section.content
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
}
