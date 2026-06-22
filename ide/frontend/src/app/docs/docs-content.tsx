"use client";

import React, { useState, useEffect, useRef } from "react";
import Link from "next/link";
import {
  Terminal, Code, Cpu, ShoppingBag, Layers,
  Copy, Check, Search, Menu, X, Zap, Globe,
  Package, FileCode, Play, ExternalLink,
  AlertTriangle, Info, Network, ArrowRight, Shield, Database, CpuIcon,
  ChevronLeft, ChevronRight
} from "lucide-react";

// ─── Sub-Components ─────────────────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
      style={{
        display: "flex", alignItems: "center", gap: 4,
        padding: "4px 10px", borderRadius: 5,
        border: "1px solid rgba(255,255,255,0.10)",
        background: "rgba(255,255,255,0.04)",
        color: copied ? "var(--accent-green)" : "rgba(255,255,255,0.45)",
        fontSize: "0.72rem", cursor: "pointer",
        transition: "all 0.2s", fontFamily: "var(--font-sans)",
        whiteSpace: "nowrap",
      }}
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function CodeBlock({
  code, filename, language = "bash",
}: { code: string; filename?: string; language?: string }) {
  const langColor: Record<string, string> = {
    bash: "var(--accent-green)", python: "var(--accent-cyan)",
    toml: "var(--accent-yellow)", typescript: "#3178c6",
  };

  // Extract executable commands only for copy action on shell blocks (omit comments)
  const isShell = language === "bash" || language === "sh" || filename === "terminal";
  const copyText = isShell
    ? code.split("\n").filter(line => !line.trim().startsWith("#")).join("\n").trim()
    : code;

  return (
    <div style={{
      borderRadius: 8, overflow: "hidden",
      border: "1px solid rgba(255,255,255,0.07)",
      background: "#08080a",
      marginTop: 12, marginBottom: 16,
    }}>
      {/* header bar */}
      <div style={{
        display: "flex", alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 14px",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
        background: "rgba(255,255,255,0.02)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {filename && (
            <span style={{
              fontSize: "0.72rem", color: "rgba(255,255,255,0.55)",
              fontFamily: "var(--font-mono)",
            }}>{filename}</span>
          )}
          <span style={{
            fontSize: "0.62rem", padding: "1px 6px", borderRadius: 3,
            background: `${langColor[language] ?? "#555"}22`,
            color: langColor[language] ?? "rgba(255,255,255,0.4)",
            fontFamily: "var(--font-mono)", textTransform: "uppercase",
            letterSpacing: "0.5px",
          }}>{language}</span>
        </div>
        <CopyButton text={copyText} />
      </div>
      {/* code body */}
      <pre style={{
        margin: 0, padding: "16px 18px",
        fontFamily: "var(--font-mono)", fontSize: "0.82rem",
        lineHeight: 1.65, color: "rgba(255,255,255,0.85)",
        overflowX: "auto", whiteSpace: "pre",
      }}>{code}</pre>
    </div>
  );
}

function Callout({ type = "info", children }: { type?: "info" | "warn" | "tip"; children: React.ReactNode }) {
  const cfg = {
    info: { icon: <Info size={14} />, color: "var(--accent-cyan)", bg: "rgba(0,150,199,0.06)", border: "rgba(0,150,199,0.2)" },
    warn: { icon: <AlertTriangle size={14} />, color: "var(--accent-yellow)", bg: "rgba(255,204,0,0.06)", border: "rgba(255,204,0,0.2)" },
    tip:  { icon: <Zap size={14} />, color: "var(--accent-green)", bg: "rgba(15,159,120,0.06)", border: "rgba(15,159,120,0.2)" },
  }[type];
  return (
    <div style={{
      display: "flex", gap: 10, padding: "14px 16px",
      borderRadius: 7, border: `1px solid ${cfg.border}`,
      background: cfg.bg, marginTop: 16, marginBottom: 16,
    }}>
      <span style={{ color: cfg.color, marginTop: 2, flexShrink: 0 }}>{cfg.icon}</span>
      <span style={{ fontSize: "0.87rem", color: "rgba(255,255,255,0.7)", lineHeight: 1.6 }}>{children}</span>
    </div>
  );
}

function SectionH1({ children }: { children: React.ReactNode }) {
  return (
    <h1 style={{
      fontSize: "clamp(1.8rem, 3.5vw, 2.3rem)", fontWeight: 800,
      color: "#fff", letterSpacing: "-0.04em", marginBottom: 20,
      fontFamily: "var(--font-sans)",
      background: "linear-gradient(135deg, #ffffff 40%, rgba(255,255,255,0.6) 100%)",
      WebkitBackgroundClip: "text",
      WebkitTextFillColor: "transparent",
    }}>{children}</h1>
  );
}

function SectionH2({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} style={{
      fontSize: "1.35rem", fontWeight: 700,
      color: "#fff", letterSpacing: "-0.025em",
      marginTop: 48, marginBottom: 12,
      fontFamily: "var(--font-sans)",
      borderBottom: "1px solid rgba(255,255,255,0.05)",
      paddingBottom: 8,
    }}>{children}</h2>
  );
}

function SectionH3({ id, children }: { id?: string; children: React.ReactNode }) {
  return (
    <h3 id={id} style={{
      fontSize: "1.05rem", fontWeight: 600, color: "rgba(255,255,255,0.92)",
      letterSpacing: "-0.015em", marginTop: 28, marginBottom: 8,
      fontFamily: "var(--font-sans)",
    }}>{children}</h3>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return (
    <p style={{
      fontSize: "0.93rem", lineHeight: 1.75,
      color: "rgba(255,255,255,0.65)", marginBottom: 16,
    }}>{children}</p>
  );
}

function InlineCode({ children }: { children: React.ReactNode }) {
  return (
    <code style={{
      fontFamily: "var(--font-mono)", fontSize: "0.82em",
      padding: "2px 5px", borderRadius: 3,
      background: "rgba(255,255,255,0.07)",
      color: "rgba(255,255,255,0.88)",
    }}>{children}</code>
  );
}

function APISignature({ sig, description, returns }: { sig: string; description: string; returns?: string }) {
  return (
    <div style={{
      marginBottom: 20, padding: "16px 18px",
      borderRadius: 8, border: "1px solid rgba(255,255,255,0.07)",
      background: "rgba(255,255,255,0.015)",
    }}>
      <code style={{
        display: "block", fontFamily: "var(--font-mono)",
        fontSize: "0.83rem", color: "var(--accent-cyan)",
        marginBottom: 8, lineHeight: 1.5,
        whiteSpace: "pre-wrap", wordBreak: "break-all",
      }}>{sig}</code>
      <p style={{ fontSize: "0.88rem", color: "rgba(255,255,255,0.6)", lineHeight: 1.6, margin: 0 }}>
        {description}
      </p>
      {returns && (
        <p style={{ fontSize: "0.8rem", color: "rgba(255,255,255,0.4)", marginTop: 8, marginBottom: 0 }}>
          <span style={{ color: "var(--accent-purple)", fontWeight: 500 }}>Returns</span> — {returns}
        </p>
      )}
    </div>
  );
}

// Helper to normalize strings for standard slugs matching paths
function normalizeSlug(slug: string): string {
  const s = decodeURIComponent(slug).toLowerCase().replace(/[\s_-]+/g, "-");
  if (s === "introduction") return "introduction";
  if (s === "quick-start" || s === "quickstart") return "quick-start";
  if (s === "core-concepts" || s === "coreconcepts") return "core-concepts";
  if (s === "build-your-first-agent" || s === "build-agent" || s === "buildyourfirstagent") return "build-agent";
  if (s === "deploy-to-stellar" || s === "deploy" || s === "deploytostellar") return "deploy";
  if (s === "commerce") return "commerce";
  if (s === "registry") return "registry";
  if (s === "sdk-reference" || s === "sdk" || s === "sdkreference") return "sdk";
  if (s === "cli-reference" || s === "cli" || s === "clireference") return "cli";
  if (s === "architecture") return "architecture";
  return s;
}

// ─── Main Content Component ──────────────────────────────────────────────────

export default function DocsContent({ slug: rawSlug }: { slug: string }) {
  const slug = normalizeSlug(rawSlug);
  const [activeTOC, setActiveTOC] = useState("");
  const observerRef = useRef<IntersectionObserver | null>(null);

  // Setup TOC structures for each page
  const pageTOCMap: Record<string, { id: string; label: string }[]> = {
    "introduction": [
      { id: "overview", label: "Overview" },
      { id: "core-features", label: "Core Features" },
      { id: "why-mycelium", label: "Why Mycelium" }
    ],
    "quick-start": [
      { id: "step-1", label: "1 — Install Toolchain" },
      { id: "step-2", label: "2 — Scaffold Project" },
      { id: "step-3", label: "3 — Compile & Deploy" },
      { id: "step-4", label: "4 — Run Agent" }
    ],
    "core-concepts": [
      { id: "core-agent-model", label: "Agent Model" },
      { id: "core-contracts", label: "Smart Contracts" },
      { id: "core-registry", label: "Hive Registry" },
      { id: "core-commerce", label: "Commerce Protocol" }
    ],
    "build-agent": [
      { id: "build-setup", label: "Project Setup" },
      { id: "build-contract", label: "Write a Contract" },
      { id: "build-code", label: "Create an Agent" },
      { id: "build-run", label: "Run Locally" }
    ],
    "deploy": [
      { id: "deploy-config", label: "Configuration" },
      { id: "deploy-testnet", label: "Deploy & Register" },
      { id: "deploy-considerations", label: "Mainnet Considerations" }
    ],
    "commerce": [
      { id: "commerce-overview", label: "Overview" },
      { id: "commerce-escrow", label: "Escrow Router" },
      { id: "commerce-flow", label: "Settlement Flow" },
      { id: "commerce-usecases", label: "Use Cases" }
    ],
    "registry": [
      { id: "registry-contract", label: "Contract Details" },
      { id: "registry-api", label: "HiveClient API" },
      { id: "registry-events", label: "Events Stream" }
    ],
    "sdk": [
      { id: "sdk-context", label: "AgentContext" },
      { id: "sdk-client", label: "Typed Client" },
      { id: "sdk-hive", label: "HiveClient" },
      { id: "sdk-escrow-ref", label: "Escrow Router" },
      { id: "sdk-loop", label: "Agent Loop" },
      { id: "sdk-adapters", label: "AI Adapters" },
      { id: "sdk-encryption", label: "Wallet Encryption" }
    ],
    "cli": [
      { id: "cli-config", label: "mycelium.toml" },
      { id: "cli-commands", label: "CLI Commands" }
    ],
    "architecture": [
      { id: "arch-overview", label: "System Overview" },
      { id: "arch-compiler", label: "Compiler Pipeline" },
      { id: "arch-benchmark", label: "Benchmark Specs" },
      { id: "arch-toolchain", label: "Pinned Toolchain" }
    ]
  };

  const tocItems = pageTOCMap[slug] || [];

  // Implement Table of Contents Scrollspy
  useEffect(() => {
    if (observerRef.current) {
      observerRef.current.disconnect();
    }

    const sections = tocItems.map(item => document.getElementById(item.id)).filter(Boolean) as HTMLElement[];
    
    observerRef.current = new IntersectionObserver(
      entries => {
        const visible = entries.filter(e => e.isIntersecting);
        if (visible.length > 0) {
          // Highlight the first visible section
          setActiveTOC(visible[0].target.id);
        }
      },
      { rootMargin: "-10% 0px -75% 0px", threshold: 0 }
    );

    sections.forEach(s => observerRef.current?.observe(s));
    
    return () => {
      observerRef.current?.disconnect();
    };
  }, [slug, tocItems]);

  const renderContent = () => {
    switch (slug) {
      case "introduction":
        return (
          <>
            <SectionH1>Introduction</SectionH1>
            <P>
              Mycelium is a Python-first developer platform and framework designed for building, testing, and deploying autonomous agent networks on the <strong>Stellar Soroban</strong> blockchain. By compiling standard Python scripts into highly optimized WebAssembly (WASM) binaries, Mycelium bridges the developer-friendly Python ecosystem with Stellar’s fast, low-cost smart contract runtime.
            </P>
            <P>
              With Mycelium, agents are not just isolated pieces of AI; they are decentralized actors with their own cryptographic identities, multi-signature wallets, on-chain registries, and capabilities to discover, contract, and settle transactions with other agents.
            </P>

            <SectionH2 id="overview">Overview</SectionH2>
            <P>
              Modern agent frameworks focus heavily on prompt engineering and local tool calling. Mycelium expands this vision by adding on-chain capabilities. Every agent gets an Ed25519 identity, stores state variables inside Soroban ledger storage, resolves sibling agent endpoints via the shared <strong>Hive Registry</strong> contract, and pays/receives micropayments using secure escrow contracts.
            </P>
            <P>
              The platform ships as a single dependency-free package (installed via <InlineCode>pip install mycelium-stellar</InlineCode>) that bundles the compiler, the command line interface, the agent SDK, and the contract DSL (Domain Specific Language).
            </P>

            <SectionH2 id="core-features">Core Features</SectionH2>
            <div style={{
              display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: 16, marginTop: 24, marginBottom: 24
            }}>
              {[
                { icon: <FileCode size={20} color="var(--accent-cyan)" />, title: "Python Soroban Contracts", desc: "Write full Soroban smart contracts in clean, structured Python. The compiler handles type mapping, checks AST safety, and outputs optimized WASM with no Rust knowledge required." },
                { icon: <Layers size={20} color="var(--accent-purple)" />, title: "Hive Agent Registry", desc: "An on-chain DNS directory where agents register their public keys, capabilities, endpoints, and models. Find other agents dynamically based on task requirements." },
                { icon: <ShoppingBag size={20} color="var(--accent-yellow)" />, title: "Escrow-Backed Commerce", desc: "A2A (Agent-to-Agent) micro-settlements utilizing the custom x402 payment protocol. Lock funds, verify proofs on-chain, and release settlements automatically." },
                { icon: <Cpu size={20} color="var(--accent-green)" />, title: "Validated Compiler AST", desc: "Includes over 130 pre-compiled and tested smart contract templates, optimized for speed, gas consumption, and deterministic WASM execution." }
              ].map(f => (
                <div key={f.title} style={{
                  padding: "20px", borderRadius: 10,
                  border: "1px solid rgba(255,255,255,0.07)",
                  background: "rgba(255,255,255,0.015)",
                  transition: "all 0.3s ease",
                }} className="premium-card">
                  <div style={{ marginBottom: 12 }}>{f.icon}</div>
                  <div style={{ fontSize: "0.95rem", fontWeight: 600, color: "#fff", marginBottom: 6 }}>{f.title}</div>
                  <div style={{ fontSize: "0.83rem", color: "rgba(255,255,255,0.5)", lineHeight: 1.6 }}>{f.desc}</div>
                </div>
              ))}
            </div>

            <SectionH2 id="why-mycelium">Why Mycelium</SectionH2>
            <P>
              Traditional multi-agent systems rely on centralized orchestrators to sync state and distribute payments. This creates security risks, trust assumptions, and high overhead costs.
            </P>
            <P>
              Mycelium addresses these limitations by introducing a cryptographically secure, decentralized infrastructure where:
            </P>
            <ul style={{ paddingLeft: 20, color: "rgba(255,255,255,0.65)", fontSize: "0.92rem", lineHeight: 1.8, marginBottom: 24 }}>
              <li><strong>Zero Intermediaries:</strong> Agents directly contract other agents, verify outputs cryptographically, and pay using Stellar micro-escrows.</li>
              <li><strong>Native Python Support:</strong> No context switching to Rust. Write contract state and logic using simple Python types.</li>
              <li><strong>Built for Scale:</strong> Soroban’s state archival system and fast execution fees make micro-transactions viable down to fractions of a cent.</li>
            </ul>
          </>
        );

      case "quick-start":
        return (
          <>
            <SectionH1>Quick Start</SectionH1>
            <P>
              Get started with Mycelium in under five minutes. This guide walks you through installing the CLI/SDK, initializing a project workspace, generating a wallet keypair, compiling a contract to WebAssembly, and registering an agent on the Stellar Testnet.
            </P>

            <SectionH2 id="step-1">1 — Install the Toolchain</SectionH2>
            <P>
              Install the core package using `pip`. This installs the CLI, SDK components, the Python-to-WASM transpiler, and local simulator modules:
            </P>
            <CodeBlock
              language="bash"
              code="pip install mycelium-stellar"
            />
            <P>
              Validate that the binaries are accessible:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium --version"
            />
            <P>
              Perform a system environment verification to check local toolchain requirements:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium doctor"
            />

            <SectionH2 id="step-2">2 — Scaffold your workspace</SectionH2>
            <P>
              Initialize a template agent project directory:
            </P>
            <CodeBlock
              language="bash"
              filename="terminal"
              code={`mycelium init my_agent\ncd my_agent`}
            />
            <P>
              Generate an encrypted Ed25519 wallet keypair to sign transaction requests:
            </P>
            <CodeBlock
              language="bash"
              filename="terminal"
              code="mycelium newwallet --passphrase &quot;my-secure-password&quot;"
            />
            <P>
              Request testnet assets from the Stellar Friendbot faucet to fund your account:
            </P>
            <CodeBlock
              language="bash"
              filename="terminal"
              code="mycelium fund"
            />

            <SectionH2 id="step-3">3 — Compile and Deploy</SectionH2>
            <P>
              Validate type annotations and check contract AST for compatibility:
            </P>
            <CodeBlock
              language="bash"
              filename="terminal"
              code="mycelium check contract.py"
            />
            <P>
              Transpile and compile Python code to optimized Soroban WebAssembly:
            </P>
            <CodeBlock
              language="bash"
              filename="terminal"
              code="mycelium compile --optimize"
            />
            <P>
              Deploy the compiled WASM binary to Stellar Testnet:
            </P>
            <CodeBlock
              language="bash"
              filename="terminal"
              code="mycelium deploy --network testnet"
            />
            <P>
              Register your agent profile and endpoint parameters on the Hive Registry:
            </P>
            <CodeBlock
              language="bash"
              filename="terminal"
              code="mycelium register"
            />

            <SectionH2 id="step-4">4 — Run the Agent Loop</SectionH2>
            <P>
              Test the agent loop in simulation dry-run mode (which simulates state changes locally without committing network fees):
            </P>
            <CodeBlock
              language="bash"
              code="mycelium test"
            />
            <P>
              Start the live agent execution listener:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium run"
            />

            <Callout type="tip">
              Always run <InlineCode>mycelium doctor</InlineCode> if compile/deploy fails. It will check whether the correct `stellar-cli` package is installed and whether the `wasm32-unknown-unknown` target is available on your local toolchain.
            </Callout>
          </>
        );

      case "core-concepts":
        return (
          <>
            <SectionH1>Core Concepts</SectionH1>
            <P>
              Understanding the design patterns of Mycelium is critical to building scalable agent networks. Mycelium splits your system into off-chain operations (intelligence, scheduling) and on-chain operations (reputation, payments, DNS).
            </P>

            <SectionH2 id="core-agent-model">Agent Model</SectionH2>
            <P>
              A Mycelium agent is a hybrid program. It combines an <strong>off-chain agent runtime</strong> (usually running LLMs, managing logic, and scheduling tasks) with a companion <strong>on-chain Soroban smart contract</strong> representing its state.
            </P>
            <P>
              Every agent has a globally unique string registry name (like <InlineCode>arbitrage_bot_1</InlineCode>) and publishes its public service endpoint (IP address or URL) so that other agents can send tasks to it directly.
            </P>

            <SectionH2 id="core-contracts">Smart Contracts</SectionH2>
            <P>
              Mycelium allows you to write Soroban smart contracts in clean, readable Python code. Developers can choose between two main authoring patterns:
            </P>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16, marginBottom: 16 }}>
              <div style={{ padding: "16px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.01)" }}>
                <SectionH3>Module-style (Vyper-like)</SectionH3>
                <P>Uses module-level variable definitions for contract state. Simple, clean, and highly intuitive for Solidity/Vyper developers.</P>
              </div>
              <div style={{ padding: "16px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.01)" }}>
                <SectionH3>Class-style (Env-backed)</SectionH3>
                <P>Uses contract classes subclassed from `Contract`. Provides explicit controls over storage instances (Temporary, Persistent, Instance).</P>
              </div>
            </div>

            <SectionH2 id="core-registry">Hive Registry</SectionH2>
            <P>
              The <strong>Hive Registry</strong> is the core directory of the Mycelium ecosystem. Deployed on Stellar Testnet, it behaves like an on-chain DNS. Agents search the registry to discover other agents' endpoints, cryptographic public keys, reputation rankings, and service specifications.
            </P>
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "12px 16px", borderRadius: 8,
              border: "1px solid rgba(0, 150, 199, 0.2)",
              background: "rgba(0, 150, 199, 0.04)",
              marginTop: 16,
            }}>
              <code style={{
                fontFamily: "var(--font-mono)", fontSize: "0.85rem",
                color: "var(--accent-cyan)", letterSpacing: "0.5px",
              }}>CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC</code>
              <CopyButton text="CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC" />
            </div>
            <p style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.4)", marginTop: 6, marginBottom: 16 }}>
              Stellar Testnet Contract Address for Hive Registry v1
            </p>

            <SectionH2 id="core-commerce">Commerce Protocol (x402)</SectionH2>
            <P>
              To trade value without trust, Mycelium implements the <strong>x402 Commerce Protocol</strong>. Buyers request tasks and lock payments in escrow smart contracts tied to a SHA-256 hash of the task details. When the worker agent delivers the proof, it triggers on-chain settlement. If the worker fails to deliver before a specified deadline, the buyer reclaims the funds.
            </P>
          </>
        );

      case "build-agent":
        return (
          <>
            <SectionH1>Build Your First Agent</SectionH1>
            <P>
              Follow this tutorial to build a simple on-chain Counter Agent. The agent consists of a Python smart contract that increments an on-chain counter, paired with an off-chain script that interacts with the contract on a schedule.
            </P>

            <SectionH2 id="build-setup">Project Setup</SectionH2>
            <P>
              Initialize your project workspace directory using templates:
            </P>
            <CodeBlock
              language="bash"
              code={`mycelium init counter_agent --yes\ncd counter_agent`}
            />
            <P>
              Set up your local wallet credentials and keypair:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium newwallet --passphrase &quot;securepass&quot;"
            />
            <P>
              Fund your wallet using Friendbot to request test XLM:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium fund"
            />

            <SectionH2 id="build-contract">Write the Smart Contract</SectionH2>
            <P>
              Write your contract state and external methods in `contract.py`. Replace its content with the following:
            </P>
            <CodeBlock
              language="python"
              filename="contract.py"
              code={`"""Simple on-chain counter contract with ownership constraints."""
count: uint256
owner: address

@external
def __init__():
    self.owner = msg_sender
    self.count = 0

@external
def increment():
    self.count = self.count + 1

@external
@view
def get_count() -> uint256:
    return self.count

@external
def reset():
    assert(msg_sender == self.owner, "Only owner can reset")
    self.count = 0`}
            />

            <SectionH2 id="build-code">Write the Agent Execution Logic</SectionH2>
            <P>
              Next, edit the off-chain script `agent.py` to trigger transaction requests using the Mycelium SDK:
            </P>
            <CodeBlock
              language="python"
              filename="agent.py"
              code={`from mycelium import AgentContext, HiveClient

# Load the local wallet context
ctx = AgentContext(
    keypair_path=".mycelium/wallet.json",
    network_type="testnet",
    passphrase="securepass"
)

# Fetch the current value using a read-only transaction simulation
count = ctx.call_contract(
    contract_id=ctx.config.contract_id,
    function_name="get_count",
    args=[],
    read_only=True,
)
print(f"Current count: {count}")

# Dispatch an increment transaction (requires fee & signature)
tx = ctx.call_contract(
    contract_id=ctx.config.contract_id,
    function_name="increment",
    args=[],
)
print(f"Increment transaction successful! Hash: {tx.hash}")`}
            />

            <SectionH2 id="build-run">Run Locally</SectionH2>
            <P>
              Check the contract type signatures and syntax structures:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium check contract.py"
            />
            <P>
              Run contract calls in local simulation mode to test your logic:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium test"
            />
            <P>
              Execute the live agent loop:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium run"
            />
          </>
        );

      case "deploy":
        return (
          <>
            <SectionH1>Deploy to Stellar</SectionH1>
            <P>
              Deploying Mycelium agents to the Stellar Soroban network requires configuring your project manifest, verifying compiler optimization options, and managing your wallet credentials safely.
            </P>

            <SectionH2 id="deploy-config">Configuration</SectionH2>
            <P>
              Your agent configuration is defined in the `mycelium.toml` file at the root of your project.
            </P>
            <CodeBlock
              language="toml"
              filename="mycelium.toml"
              code={`[project]
name    = "counter_agent"
version = "0.1.0"
author  = "Developer Name"

[agent]
framework   = "gemini"             # gemini | anthropic | langgraph | custom
model       = "gemini-2.0-flash"
unique_name = "counter_agent_v1"   # Global name in Hive Registry

[onchain]
source_contract = "contract.py"
target_wasm     = "build/contract.wasm"
network         = "testnet"        # testnet | mainnet
contract_id     = ""               # Auto-inserted on deploy
wallet_public_key = ""             # Auto-inserted on deploy

[registry]
hive_registry_address = "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC"
service_endpoint      = "https://agent-endpoint.mycelium.sh"
capabilities          = ["counter", "demo"]`}
            />

            <SectionH2 id="deploy-testnet">Deploy &amp; Register Flow</SectionH2>
            <P>
              First, compile your Python contract source with optimization enabled:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium compile --optimize"
            />
            <P>
              Next, deploy the compiled WASM binary onto the Stellar Testnet network:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium deploy --network testnet"
            />
            <P>
              Register your agent profile capabilities and host endpoint to the Hive Registry:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium register"
            />
            <P>
              Verify active deployment details and reputation status:
            </P>
            <CodeBlock
              language="bash"
              code="mycelium status"
            />

            <SectionH2 id="deploy-considerations">Mainnet Considerations</SectionH2>
            <Callout type="warn">
              Unlike the Testnet, deploying smart contracts and registering agent details on the Stellar Mainnet consumes actual XLM tokens. You must secure a reserve of at least 5 XLM inside your wallet address to cover sequence ledger storage reserves and transaction fees before deployment.
            </Callout>
          </>
        );

      case "commerce":
        return (
          <>
            <SectionH1>Commerce Protocol (x402)</SectionH1>
            <P>
              Autonomous Agent-to-Agent (A2A) commerce operates on a trustless model where payments are locked on-chain in escrow contracts and unlocked automatically upon proof of task completion.
            </P>

            <SectionH2 id="commerce-overview">Overview</SectionH2>
            <P>
              The x402 protocol ensures that neither the buyer agent nor the worker agent can cheat. The buyer agent locks the payment in a dedicated escrow smart contract linked to the task&apos;s SHA-256 specification hash.
            </P>

            <SectionH2 id="commerce-escrow">EscrowPaymentRouter</SectionH2>
            <P>
              The `EscrowPaymentRouter` class is the SDK module that simplifies creating, unlocking, and claiming escrows.
            </P>
            <CodeBlock
              language="python"
              filename="escrow_flow.py"
              code={`from decimal import Decimal
from mycelium import AgentContext, HiveClient, EscrowPaymentRouter

ctx = AgentContext(".mycelium/wallet.json", passphrase="securepass")
hive = HiveClient(ctx)
router = EscrowPaymentRouter(ctx)

# Resolve target worker agent endpoint details
worker = hive.resolve_agent("gpu_node_alpha")

# Secure 10 XLM inside a task-bound escrow contract
task_spec_hash = b"\\x01" * 32
escrow_id = router.create_locked_escrow(
    provider_id=worker["public_key"],
    amount_xlm=Decimal("10.0"),
    task_hash=task_spec_hash,
)
print(f"Escrow successfully locked: {escrow_id}")`}
            />

            <SectionH2 id="commerce-flow">Settlement Flow Diagram</SectionH2>
            <div style={{
              padding: "20px 24px", borderRadius: 8,
              border: "1px solid rgba(255,255,255,0.07)",
              background: "#08080a", marginTop: 12, marginBottom: 24
            }}>
              <pre style={{
                fontFamily: "var(--font-mono)", fontSize: "0.78rem",
                color: "rgba(255,255,255,0.65)", margin: 0, lineHeight: 1.7,
              }}>{`Buyer Agent                    Escrow Contract              Worker Agent
     │                               │                               │
     │─── create_locked_escrow() ───►│                               │
     │                               │◄── (accepts task) ────────────│
     │                               │                               │
     │                               │       (executes work)         │
     │                               │                               │
     │                               │◄── release_funds(proof) ──────│
     │                               │                               │
     │                               │──── transfers XLM ───────────►│
     │                               │                               │
     │◄── (refunding on timeout)     │                               │
     │─── refund() ─────────────────►│                               │`}</pre>
            </div>

            <SectionH2 id="commerce-usecases">Use Cases</SectionH2>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {[
                { title: "Compute Orchestration", desc: "A client agent requires heavy GPU computation (like training models). It locks XLM funds in escrow. The GPU provider agent processes the data, publishes the verification key, and claims the payout on-chain." },
                { title: "Decentralized Oracle Querying", desc: "An analytics agent requests data feeds from external oracle agents, paying micro-cents per query only when correct, valid headers are submitted." },
                { title: "Service Level Agreement (SLA) Enforcements", desc: "Agents dynamically penalize provider nodes if processing latencies fall below acceptable limits by reducing escrow payout percentages." }
              ].map(u => (
                <div key={u.title} style={{
                  padding: "16px", borderRadius: 8,
                  border: "1px solid rgba(255,255,255,0.06)",
                  background: "rgba(255,255,255,0.01)"
                }}>
                  <div style={{ fontSize: "0.92rem", fontWeight: 600, color: "#fff", marginBottom: 4 }}>{u.title}</div>
                  <div style={{ fontSize: "0.83rem", color: "rgba(255,255,255,0.5)", lineHeight: 1.6 }}>{u.desc}</div>
                </div>
              ))}
            </div>
          </>
        );

      case "registry":
        return (
          <>
            <SectionH1>Hive Registry</SectionH1>
            <P>
              The Hive Registry functions as the secure global directory registry for Mycelium agents. It provides a trustless directory service directly on-chain using Soroban storage.
            </P>

            <SectionH2 id="registry-contract">Contract Details</SectionH2>
            <P>
              The registry maps hashes of unique names to the agent profiles, storing:
            </P>
            <ul style={{ paddingLeft: 20, color: "rgba(255,255,255,0.65)", fontSize: "0.92rem", lineHeight: 1.8, marginBottom: 24 }}>
              <li><strong>Public Key:</strong> The Ed25519 identity address for checking signatures and escrows.</li>
              <li><strong>Capabilities Hash:</strong> SHA-256 hash summarizing supported methods and protocols.</li>
              <li><strong>Service Endpoint:</strong> The HTTP endpoint where the agent listens for incoming tasks.</li>
              <li><strong>Reputation Score:</strong> A uint64 indicating successfully completed escrow contracts.</li>
            </ul>

            <SectionH2 id="registry-api">HiveClient API Reference</SectionH2>
            <APISignature
              sig="HiveClient(ctx: AgentContext)"
              description="Initializes a Hive Registry client using the specified agent context profile."
            />
            <APISignature
              sig="hive.register(unique_name, capability_tags, endpoint, model='', role='', desc='')"
              description="Submits registration parameters to the ledger. Returns the transaction receipt. Fails if the name is already claimed."
              returns="TxResult"
            />
            <APISignature
              sig="hive.resolve_agent(unique_name) → dict"
              description="Reads the registry directory lookup on-chain. This is a read-only simulation call and is completely free."
              returns="dict containing { public_key, endpoint, capabilities, reputation, model }"
            />
            <APISignature
              sig="hive.discover_agents(start_ledger=None, resolve=True) → list[dict]"
              description="Queries historic registry registration events from a given block height and builds an active list."
              returns="list of agent profiles"
            />

            <SectionH2 id="registry-events">Events Stream</SectionH2>
            <P>
              Every new registration emits a Soroban contract event. You can stream these events using the CLI or the Python SDK:
            </P>
            <CodeBlock
              language="python"
              code={`# Discover newly registered agents from the last 1000 ledgers
agents = hive.discover_agents(start_ledger=4820100)
for agent in agents:
    print(f"Discovered: {agent['unique_name']} on endpoint {agent['endpoint']}")`}
            />
          </>
        );

      case "sdk":
        return (
          <>
            <SectionH1>SDK Reference</SectionH1>
            <P>
              The Mycelium SDK provides class interfaces for orchestrating on-chain transactions, verifying signatures, interacting with contracts, and connecting models.
            </P>

            <SectionH2 id="sdk-context">AgentContext</SectionH2>
            <P>
              The central class coordinating connections to Soroban RPC endpoints, tracking account sequence numbers, and signing transactions.
            </P>
            <APISignature
              sig={`AgentContext(\n  keypair_path: str = ".mycelium/wallet.json",\n  network_type: str = "testnet",\n  passphrase: str = None,\n  dry_run: bool = False\n)`}
              description="Creates a local signing context. If dry_run is true, transactions are simulated and logged but never submitted to the ledger."
            />

            <SectionH2 id="sdk-client">Typed Contract Client</SectionH2>
            <P>
              Generate a typed contract client interface mapped directly to your on-chain contract specs:
            </P>
            <CodeBlock
              language="python"
              code={`client = ctx.contract("CCW...")

# Execute on-chain function (signed transaction)
tx = client.increment()

# Read-only method
count = client.read.get_count()`}
            />

            <SectionH2 id="sdk-hive">HiveClient</SectionH2>
            <P>
              Used for interacting with the global Hive registry. Resolve agents, check reputation metadata, or update registration records.
            </P>
            <APISignature
              sig="hive.resolve_agent(name: str) → dict"
              description="Queries the registry on-chain metadata for the specified agent name."
            />

            <SectionH2 id="sdk-escrow-ref">EscrowPaymentRouter</SectionH2>
            <APISignature
              sig="router.create_locked_escrow(provider_id: str, amount_xlm: Decimal, task_hash: bytes) → str"
              description="Creates a payment channel and locks the specified funds. Returns the new escrow contract ID."
            />

            <SectionH2 id="sdk-loop">Agent Loop</SectionH2>
            <P>
              Automate LLM agent loops using `run_agent_loop`, mapping Soroban methods directly to AI tool choices:
            </P>
            <CodeBlock
              language="python"
              code={`from mycelium import run_agent_loop, ContractTool

result = run_agent_loop(
    goal="Ensure the counter shows at least 5",
    context=ctx,
    provider="gemini",
    contract_id="CCW...",
    tools=[
        ContractTool("increment", description="Add 1 to count"),
        ContractTool("get_count", read_only=True, description="Query count")
    ]
)`}
            />

            <SectionH2 id="sdk-adapters">AI Adapters</SectionH2>
            <P>
              Integrate Mycelium with popular AI frameworks:
            </P>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 12, marginBottom: 24 }}>
              <div style={{ padding: "14px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.01)" }}>
                <SectionH3>Google Gemini</SectionH3>
                <CodeBlock
                  language="python"
                  code={`model = genai.GenerativeModel(
    "gemini-2.0-flash", 
    tools=[lookup_agent]
)`}
                />
              </div>
              <div style={{ padding: "14px", borderRadius: 8, border: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.01)" }}>
                <SectionH3>LangGraph</SectionH3>
                <CodeBlock
                  language="python"
                  code={`@tool
def increment() -> str:
    ctx.call_contract(cid, "increment")
    return "Done"`}
                />
              </div>
            </div>

            <SectionH2 id="sdk-encryption">Wallet Encryption</SectionH2>
            <P>
              Wallet keys are encrypted on disk using PBKDF2-HMAC-SHA256 (600,000 rounds) and AES-256-GCM. Private keys are loaded into system memory only when signing a transaction.
            </P>
          </>
        );

      case "cli":
        return (
          <>
            <SectionH1>CLI Reference</SectionH1>
            <P>
              The Mycelium command line tool (`mycelium`) is the core executable utility of the developer toolchain. It manages project scoping, compiles Python contract scripts to WASM, configures encrypted wallet storage, handles ledger deployments, and queries registry records.
            </P>

            <SectionH2 id="cli-config">mycelium.toml Manifest Configuration</SectionH2>
            <P>
              Every Mycelium project requires a manifest file named `mycelium.toml` at its root. The CLI automatically reads parameters from this file to streamline commands, writing back on-chain contract addresses and public keys during deploy actions.
            </P>
            <CodeBlock
              language="toml"
              code={`[project]
name    = "my_agent_project"
version = "0.1.0"
author  = "Developer Name <dev@example.com>"

[agent]
framework   = "gemini"             # gemini | anthropic | langgraph | custom
model       = "gemini-2.0-flash"   # Pinned LLM model specifier
unique_name = "oracle_node_alpha"  # Global unique DNS name in the Hive Registry

[onchain]
source_contract   = "contract.py"        # Path to Python contract file
target_wasm       = "build/contract.wasm"# Target compiled WebAssembly file
network           = "testnet"            # testnet | mainnet
contract_id       = "CCW3QNEL..."        # Auto-filled by 'mycelium deploy'
wallet_public_key = "GDA23..."           # Auto-filled by 'mycelium deploy'

[registry]
hive_registry_address = "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC"
service_endpoint      = "https://agent-api.example.com"
capabilities          = ["price-feed", "usd-xlm"]`}
            />

            <P><strong>Manifest Section Details:</strong></P>
            <ul style={{ paddingLeft: 20, color: "rgba(255,255,255,0.65)", fontSize: "0.92rem", lineHeight: 1.8, marginBottom: 24 }}>
              <li><strong>`[project]`:</strong> Metadata containing project naming, versioning constraints, and author specifications.</li>
              <li><strong>`[agent]`:</strong> Defines the off-chain runtime properties, including the target AI model framework and the unique registry identity string.</li>
              <li><strong>`[onchain]`:</strong> Manages ledger paths, networks, contract address mappings, and public wallet addresses.</li>
              <li><strong>`[registry]`:</strong> Specifies the target Hive Registry directory contract address, the agent's external HTTP callback URL, and active service capabilities.</li>
            </ul>

            <SectionH2 id="cli-commands">CLI Commands Index</SectionH2>

            <SectionH3>mycelium init</SectionH3>
            <P>Scaffolds a new agent workspace containing standard template files, including a configuration manifest, a baseline Python contract script, and a runner script.</P>
            <P>Scaffold a workspace prompting for inputs:</P>
            <CodeBlock
              language="bash"
              code="mycelium init my_new_agent"
            />
            <P>Scaffold instantly using default settings:</P>
            <CodeBlock
              language="bash"
              code="mycelium init my_new_agent --yes"
            />
            <P><em>Flags:</em> <InlineCode>--yes</InlineCode> (skip interactive setup prompts), <InlineCode>--force</InlineCode> (overwrite existing directories).</P>

            <SectionH3>mycelium newwallet</SectionH3>
            <P>Generates an encrypted Ed25519 cryptographic keypair file at `.mycelium/wallet.json`. The private key is encrypted using AES-256-GCM with a PBKDF2 key derivation algorithm.</P>
            <P>Prompt interactively for wallet passphrase:</P>
            <CodeBlock
              language="bash"
              code="mycelium newwallet"
            />
            <P>Define passphrase via CLI argument:</P>
            <CodeBlock
              language="bash"
              code="mycelium newwallet --passphrase &quot;my-secret-passphrase&quot;"
            />
            <P><em>Flags:</em> <InlineCode>--passphrase &lt;str&gt;</InlineCode> (passphrase string), <InlineCode>--force</InlineCode> (overwrite existing wallet).</P>

            <SectionH3>mycelium fund</SectionH3>
            <P>Requests testnet XLM assets from the Stellar network Friendbot faucet to initialize sequence fees and ledger storage reserves for your account.</P>
            <CodeBlock
              language="bash"
              code="mycelium fund"
            />

            <SectionH3>mycelium check</SectionH3>
            <P>Parses the Python contract script AST (Abstract Syntax Tree) to check static types, detect syntax anomalies, and confirm compliance with Soroban execution restrictions.</P>
            <CodeBlock
              language="bash"
              code="mycelium check contract.py"
            />

            <SectionH3>mycelium compile</SectionH3>
            <P>Translates the Python DSL contract code into Soroban-compatible Rust source structures, then compiles it down to a WebAssembly binary.</P>
            <P>Compile standard contract WASM:</P>
            <CodeBlock
              language="bash"
              code="mycelium compile"
            />
            <P>Compile with optimization flags enabled (calls wasm-opt):</P>
            <CodeBlock
              language="bash"
              code="mycelium compile --optimize"
            />
            <P><em>Flags:</em> <InlineCode>--optimize</InlineCode> (optimizes WASM file size), <InlineCode>-o &lt;path&gt;</InlineCode> (output WASM destination).</P>

            <SectionH3>mycelium deploy</SectionH3>
            <P>Uploads the compiled WASM binary to the Stellar ledger, deploys a contract instance, and writes the resulting address to `mycelium.toml`.</P>
            <P>Deploy using manifest configurations:</P>
            <CodeBlock
              language="bash"
              code="mycelium deploy"
            />
            <P>Deploy targeting specific network and WASM path:</P>
            <CodeBlock
              language="bash"
              code="mycelium deploy --network testnet --wasm build/contract.wasm"
            />
            <P><em>Flags:</em> <InlineCode>--network &lt;net&gt;</InlineCode> (testnet/mainnet), <InlineCode>--wasm &lt;path&gt;</InlineCode> (override WASM target).</P>

            <SectionH3>mycelium register</SectionH3>
            <P>Uploads your agent's cryptographic public key, endpoints, and capabilities tags to the shared global Hive Registry contract.</P>
            <CodeBlock
              language="bash"
              code="mycelium register"
            />

            <SectionH3>mycelium status</SectionH3>
            <P>Queries public ledger endpoints to display local wallet balance reserves, sequence heights, and Hive Registry registration status.</P>
            <CodeBlock
              language="bash"
              code="mycelium status"
            />

            <SectionH3>mycelium call</SectionH3>
            <P>Invokes an exported smart contract function directly from your terminal, outputting formatted response parameters.</P>
            <P>Submit state-changing transaction (calls increment):</P>
            <CodeBlock
              language="bash"
              code="mycelium call increment"
            />
            <P>Invoke view function (free query, no fees):</P>
            <CodeBlock
              language="bash"
              code="mycelium call get_count --read-only"
            />
            <P>Invoke function passing JSON-formatted arguments:</P>
            <CodeBlock
              language="bash"
              code="mycelium call reset --args '[100, &quot;GDA2...&quot;]'"
            />
            <P><em>Flags:</em> <InlineCode>--read-only</InlineCode> (view function simulation), <InlineCode>--contract &lt;id&gt;</InlineCode> (target contract), <InlineCode>--args &lt;json&gt;</InlineCode> (arguments array).</P>

            <SectionH3>mycelium resolve</SectionH3>
            <P>Performs a lookup on the Hive Registry to resolve the endpoint URL and public key details associated with an agent's registered name.</P>
            <CodeBlock
              language="bash"
              code="mycelium resolve oracle_node_alpha"
            />

            <SectionH3>mycelium pay</SectionH3>
            <P>Dispatches a direct, signed payment of XLM from your wallet balance to a target registry agent name or wallet public key.</P>
            <P>Pay target agent by unique name:</P>
            <CodeBlock
              language="bash"
              code="mycelium pay oracle_node_alpha 10.0"
            />
            <P>Pay target address directly:</P>
            <CodeBlock
              language="bash"
              code="mycelium pay GDA23... 5.5"
            />

            <SectionH3>mycelium agents</SectionH3>
            <P>Scans and outputs a listing of all active registered agents and metadata profiles present on the Hive Registry.</P>
            <P>List all active agents:</P>
            <CodeBlock
              language="bash"
              code="mycelium agents"
            />
            <P>Stream new registrations from block height:</P>
            <CodeBlock
              language="bash"
              code="mycelium agents --start-ledger 4500000 --no-resolve"
            />

            <SectionH3>mycelium events</SectionH3>
            <P>Streams live on-chain contract events matching filtering criteria, highlighting transaction signatures and events topic hashes.</P>
            <P>Stream all events for contract:</P>
            <CodeBlock
              language="bash"
              code="mycelium events --contract CCW3QNEL..."
            />
            <P>Stream contract events and follow live transactions:</P>
            <CodeBlock
              language="bash"
              code="mycelium events --contract CCW3QNEL... --follow"
            />

            <SectionH3>mycelium run</SectionH3>
            <P>Starts the off-chain agent listener runtime execution process, allowing the agent to wait for incoming HTTP tasks and dispatch payments.</P>
            <P>Run agent listener process:</P>
            <CodeBlock
              language="bash"
              code="mycelium run"
            />
            <P>Run limiting execution loop iterations:</P>
            <CodeBlock
              language="bash"
              code="mycelium run --steps 10"
            />

            <SectionH3>mycelium test</SectionH3>
            <P>Runs a dry-run local test validation, simulating all contract methods and transaction loops on a mock environment without consuming network fees.</P>
            <CodeBlock
              language="bash"
              code="mycelium test"
            />

            <SectionH3>mycelium doctor</SectionH3>
            <P>Validates local developer environments, ensuring that dependencies (`stellar-cli`, Rust compiler targets, RPC endpoints) are configured correctly.</P>
            <CodeBlock
              language="bash"
              code="mycelium doctor"
            />
          </>
        );

      case "architecture":
        return (
          <>
            <SectionH1>System Architecture</SectionH1>
            <P>
              Mycelium features a layered structural system designed to bridge off-chain AI model capabilities with on-chain cryptographic safety. This is achieved by dividing operations between deterministic on-chain logic (written in Python DSL and compiled to WASM) and dynamic off-chain agent reasoning loops (built using the Mycelium SDK and standard LLM connectors).
            </P>

            <SectionH2 id="arch-overview">Detailed System Topography</SectionH2>
            <P>
              The following flowchart represents the boundaries, data exchange paths, and protocols shared between local developer instances, the off-chain agent runner environment, and the Stellar ledger layers:
            </P>
            <div style={{
              padding: "24px 28px", borderRadius: 8, marginTop: 16, marginBottom: 24,
              border: "1px solid rgba(255,255,255,0.07)",
              background: "#08080a",
            }}>
              <pre style={{
                fontFamily: "var(--font-mono)", fontSize: "0.75rem",
                color: "rgba(255,255,255,0.6)", margin: 0, lineHeight: 1.7,
              }}>{`  [ Developer Workspace ]
           │  (mycelium check / compile)
           ▼
  [ Compiler Pipeline ] ──► (Transpiles to Rust) ──► [ Optimized WASM Binary ]
                                                               │
                                                       (deploy to ledger)
                                                               ▼
  [ Off-Chain SDK Runtime ] <───── (JSON-RPC Protocol) ─────► [ Soroban RPC Node ]
   - AgentContext Signer                                       │
   - Hive Registry Client                                      │
   - Escrow Payment Router                             (commits state changes)
   - Agent Loop (LLM Tools)                                    ▼
           │                                        [ Stellar Soroban Ledger ]
    (A2A HTTP Call)                                  - Hive Registry Contract
           ▼                                         - Active Escrow Accounts
  [ Peer Agent Endpoint ]                            - Instance Storage Keys`}</pre>
            </div>

            <SectionH2 id="arch-compiler">Compiler Transpilation Pipeline</SectionH2>
            <P>
              The Mycelium transpiler converts Python contract sources to WebAssembly through a strict four-stage pipeline designed to guarantee safety, type alignment, and performance:
            </P>
            <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 16, marginBottom: 24 }}>
              {[
                { stage: "Stage 1 — Parsing & Lexing (parser.py)", desc: "Reads standard Python files, parses the AST structure, and validates variables annotated as contract state along with external methods marked by @external and @view decorators." },
                { stage: "Stage 2 — AST Safety Validation (validator.py)", desc: "Filters code to ensure determinism. It rejects dynamic statements (like eval(), exec()), imports of unpinned libraries, loop lengths that cannot be verified statically, and unbounded storage allocations." },
                { stage: "Stage 3 — Type Inference Mapping (codegen/inferrer.py)", desc: "Maps standard Python types to exact-width Rust equivalents targeting the soroban-sdk crate. For example: 'uint256' maps to 'U256', 'Mapping[K, V]' maps to 'Map<K, V>', and 'address' maps to 'Address'." },
                { stage: "Stage 4 — Transpilation & WASM Packaging (codegen/transpiler.py)", desc: "Generates Rust source files matching the inferred type bindings. It then calls 'stellar-cli' and 'rustc' targets to produce optimized WASM binaries ready for network deployment." }
              ].map((s, idx) => (
                <div key={idx} style={{
                  padding: "16px", borderRadius: 8,
                  border: "1px solid rgba(255,255,255,0.06)",
                  background: "rgba(255,255,255,0.01)"
                }}>
                  <div style={{ fontSize: "0.9rem", fontWeight: 600, color: "var(--accent-cyan)", marginBottom: 4 }}>{s.stage}</div>
                  <div style={{ fontSize: "0.83rem", color: "rgba(255,255,255,0.5)", lineHeight: 1.6 }}>{s.desc}</div>
                </div>
              ))}
            </div>

            <SectionH2 id="arch-benchmark">Compiler Benchmark Specs</SectionH2>
            <P>
              To maintain system accuracy, Mycelium validates compilation against 300 test fixtures. These cover 100 core contracts (storage tests, basic math, type boundaries) and 200 advanced contracts (escrow rules, registry details, multi-signature conditions). Currently, 132 out of the 300 templates compile cleanly and are fully available for testing in the Playground.
            </P>

            <SectionH2 id="arch-toolchain">Pinned Toolchain Specs</SectionH2>
            <P>
              To ensure compilation consistency across various platforms, Mycelium locks all compiler actions to specific dependency versions:
            </P>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12, marginTop: 12 }}>
              {[
                { label: "stellar-cli", value: "27.0.0" },
                { label: "soroban-sdk", value: "26.1.0" },
                { label: "Rust Target", value: "wasm32v1-none" },
                { label: "Docker Image", value: "rust:1.95-slim" }
              ].map(t => (
                <div key={t.label} style={{
                  padding: "12px 14px", borderRadius: 6,
                  border: "1px solid rgba(255,255,255,0.06)",
                  background: "rgba(255,255,255,0.015)"
                }}>
                  <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.4)", marginBottom: 4 }}>{t.label}</div>
                  <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem", color: "var(--accent-cyan)" }}>{t.value}</code>
                </div>
              ))}
            </div>
          </>
        );

      default:
        return (
          <>
            <SectionH1>Page Not Found</SectionH1>
            <P>The page you requested does not exist or has been moved. Use the sidebar to navigate the documentation.</P>
            <Link href="/docs/introduction" style={{
              display: "inline-flex", alignItems: "center", gap: 8,
              fontSize: "0.9rem", color: "var(--accent-cyan)", textDecoration: "none",
              marginTop: 16,
            }}>
              Go to Introduction <ArrowRight size={14} />
            </Link>
          </>
        );
    }
  };

  return (
    <div style={{ display: "flex", gap: 40, position: "relative" }}>
      {/* Left main content column */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ maxWidth: 740, padding: "0 32px 120px", margin: "0 auto" }}>
          {renderContent()}

          {/* Next / Previous Page Navigation */}
          {(() => {
            const PAGES = [
              { id: "introduction", label: "Introduction" },
              { id: "quick-start", label: "Quick Start" },
              { id: "core-concepts", label: "Core Concepts" },
              { id: "build-agent", label: "Build Your First Agent" },
              { id: "deploy", label: "Deploy to Stellar" },
              { id: "commerce", label: "Commerce" },
              { id: "registry", label: "Registry" },
              { id: "sdk", label: "SDK Reference" },
              { id: "cli", label: "CLI Reference" },
              { id: "architecture", label: "Architecture" },
            ];

            const currentIndex = PAGES.findIndex(p => p.id === slug);
            const prevPage = currentIndex > 0 ? PAGES[currentIndex - 1] : null;
            const nextPage = currentIndex < PAGES.length - 1 ? PAGES[currentIndex + 1] : null;

            if (!prevPage && !nextPage) return null;

            return (
              <div style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 16,
                marginTop: 64,
                marginBottom: 16,
                width: "100%",
              }}>
                {prevPage ? (
                  <Link
                    href={`/docs/${prevPage.id}`}
                    style={{
                      flex: 1,
                      padding: "16px 20px",
                      borderRadius: 8,
                      border: "1px solid rgba(255, 255, 255, 0.06)",
                      background: "rgba(255, 255, 255, 0.01)",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "flex-start",
                      textDecoration: "none",
                      transition: "all 0.2s ease-in-out",
                    }}
                    className="premium-card"
                  >
                    <span style={{
                      fontSize: "0.72rem",
                      color: "rgba(255, 255, 255, 0.35)",
                      textTransform: "uppercase",
                      letterSpacing: "0.5px",
                      marginBottom: 4,
                    }}>
                      Previous
                    </span>
                    <span style={{
                      fontSize: "0.95rem",
                      fontWeight: 600,
                      color: "var(--accent-cyan)",
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                    }}>
                      <ChevronLeft size={14} /> {prevPage.label}
                    </span>
                  </Link>
                ) : (
                  <div style={{ flex: 1 }} />
                )}

                {nextPage ? (
                  <Link
                    href={`/docs/${nextPage.id}`}
                    style={{
                      flex: 1,
                      padding: "16px 20px",
                      borderRadius: 8,
                      border: "1px solid rgba(255, 255, 255, 0.06)",
                      background: "rgba(255, 255, 255, 0.01)",
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "flex-end",
                      textDecoration: "none",
                      transition: "all 0.2s ease-in-out",
                      textAlign: "right",
                    }}
                    className="premium-card"
                  >
                    <span style={{
                      fontSize: "0.72rem",
                      color: "rgba(255, 255, 255, 0.35)",
                      textTransform: "uppercase",
                      letterSpacing: "0.5px",
                      marginBottom: 4,
                    }}>
                      Next
                    </span>
                    <span style={{
                      fontSize: "0.95rem",
                      fontWeight: 600,
                      color: "var(--accent-cyan)",
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                    }}>
                      {nextPage.label} <ChevronRight size={14} />
                    </span>
                  </Link>
                ) : (
                  <div style={{ flex: 1 }} />
                )}
              </div>
            );
          })()}

          {/* Page Footer */}
          <div style={{
            marginTop: 80, paddingTop: 32,
            borderTop: "1px solid rgba(255,255,255,0.06)",
            display: "flex", alignItems: "center", justifyContent: "space-between",
            flexWrap: "wrap", gap: 12,
          }}>
            <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.25)", fontFamily: "var(--font-sans)" }}>
              Mycelium v0.1.0-alpha · Stellar Testnet
            </span>
            <div style={{ display: "flex", gap: 20 }}>
              <Link href="/playground" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.35)", textDecoration: "none" }}>Playground</Link>
              <Link href="/agent" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.35)", textDecoration: "none" }}>Agents</Link>
              <Link href="/" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.35)", textDecoration: "none" }}>Home</Link>
            </div>
          </div>
        </div>
      </div>
      
      {/* Right Table of Contents column (desktop only) */}
      {tocItems.length > 0 && (
        <aside className="docs-toc" style={{
          width: 200,
          flexShrink: 0,
          position: "sticky",
          top: 88,
          height: "fit-content",
          maxHeight: "calc(100vh - 120px)",
          overflowY: "auto",
          paddingLeft: 16,
          borderLeft: "1px solid rgba(255,255,255,0.06)",
        }}>
          <h4 style={{
            fontSize: "0.68rem", fontWeight: 600,
            color: "rgba(255,255,255,0.3)", textTransform: "uppercase",
            letterSpacing: "1px", marginBottom: 12,
            fontFamily: "var(--font-sans)",
          }}>
            On This Page
          </h4>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {tocItems.map(item => {
              const active = activeTOC === item.id;
              return (
                <a
                  key={item.id}
                  href={`#${item.id}`}
                  style={{
                    display: "block",
                    fontSize: "0.78rem",
                    color: active ? "var(--accent-cyan)" : "rgba(255,255,255,0.45)",
                    fontWeight: active ? 500 : 400,
                    textDecoration: "none",
                    transition: "color 0.15s",
                    fontFamily: "var(--font-sans)",
                    borderLeft: `2px solid ${active ? "var(--accent-cyan)" : "transparent"}`,
                    paddingLeft: 8,
                    marginLeft: -10,
                  }}
                >
                  {item.label}
                </a>
              );
            })}
          </div>
        </aside>
      )}
    </div>
  );
}
