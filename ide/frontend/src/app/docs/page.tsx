"use client";

import React, { useState, useEffect, useRef } from "react";
import Link from "next/link";
import {
  Terminal, Code, Cpu, ShoppingBag, Layers,
  Copy, Check, Search, Menu, X,
  Zap, Globe, Package,
  FileCode, Play,
  ExternalLink,
  AlertTriangle, Info, Network,
} from "lucide-react";

// ─── Primitive components ────────────────────────────────────────────────────

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
  return (
    <div style={{
      borderRadius: 8, overflow: "hidden",
      border: "1px solid rgba(255,255,255,0.07)",
      background: "#08080a",
      marginTop: 12, marginBottom: 4,
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
        <CopyButton text={code} />
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
      display: "flex", gap: 10, padding: "12px 14px",
      borderRadius: 7, border: `1px solid ${cfg.border}`,
      background: cfg.bg, marginTop: 16, marginBottom: 4,
    }}>
      <span style={{ color: cfg.color, marginTop: 2, flexShrink: 0 }}>{cfg.icon}</span>
      <span style={{ fontSize: "0.87rem", color: "rgba(255,255,255,0.7)", lineHeight: 1.6 }}>{children}</span>
    </div>
  );
}

function SectionAnchor({ id }: { id: string }) {
  return <span id={id} data-section style={{ position: "relative", top: -90 }} />;
}

function SectionH1({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h1 id={id} data-section style={{
      fontSize: "clamp(1.7rem, 3vw, 2.1rem)", fontWeight: 800,
      color: "#fff", letterSpacing: "-0.04em", marginBottom: 10,
      paddingTop: 72, fontFamily: "var(--font-sans)",
    }}>{children}</h1>
  );
}

function SectionH2({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} data-section style={{
      fontSize: "1.25rem", fontWeight: 700,
      color: "#fff", letterSpacing: "-0.025em", marginBottom: 8,
      paddingTop: 52, fontFamily: "var(--font-sans)",
    }}>{children}</h2>
  );
}

function SectionH3({ children }: { children: React.ReactNode }) {
  return (
    <h3 style={{
      fontSize: "1rem", fontWeight: 600, color: "rgba(255,255,255,0.9)",
      letterSpacing: "-0.015em", marginBottom: 6, paddingTop: 28,
      fontFamily: "var(--font-sans)",
    }}>{children}</h3>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return (
    <p style={{
      fontSize: "0.93rem", lineHeight: 1.75,
      color: "rgba(255,255,255,0.65)", marginBottom: 12,
    }}>{children}</p>
  );
}

function InlineCode({ children }: { children: React.ReactNode }) {
  return (
    <code style={{
      fontFamily: "var(--font-mono)", fontSize: "0.82em",
      padding: "1px 5px", borderRadius: 3,
      background: "rgba(255,255,255,0.07)",
      color: "rgba(255,255,255,0.88)",
    }}>{children}</code>
  );
}

function Divider() {
  return <div style={{ height: 1, background: "rgba(255,255,255,0.06)", margin: "40px 0 0" }} />;
}

function APISignature({ sig, description, returns }: { sig: string; description: string; returns?: string }) {
  return (
    <div style={{
      marginBottom: 20, padding: "14px 16px",
      borderRadius: 8, border: "1px solid rgba(255,255,255,0.07)",
      background: "rgba(255,255,255,0.015)",
    }}>
      <code style={{
        display: "block", fontFamily: "var(--font-mono)",
        fontSize: "0.83rem", color: "var(--accent-cyan)",
        marginBottom: 8, lineHeight: 1.5,
        whiteSpace: "pre-wrap", wordBreak: "break-all",
      }}>{sig}</code>
      <p style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.6)", lineHeight: 1.6, margin: 0 }}>
        {description}
      </p>
      {returns && (
        <p style={{ fontSize: "0.8rem", color: "rgba(255,255,255,0.4)", marginTop: 6, marginBottom: 0 }}>
          <span style={{ color: "var(--accent-purple)" }}>Returns</span> — {returns}
        </p>
      )}
    </div>
  );
}

// ─── Navigation definition ───────────────────────────────────────────────────

const NAV = [
  { id: "introduction",        label: "Introduction",           icon: <Globe size={14} /> },
  { id: "quick-start",         label: "Quick Start",            icon: <Zap size={14} /> },
  {
    id: "core-concepts", label: "Core Concepts", icon: <Package size={14} />,
    children: [
      { id: "core-agent-model",  label: "Agent Model" },
      { id: "core-contracts",    label: "Smart Contracts" },
      { id: "core-registry",     label: "Hive Registry" },
      { id: "core-commerce",     label: "Commerce Protocol" },
    ],
  },
  {
    id: "build-agent", label: "Build Your First Agent", icon: <Play size={14} />,
    children: [
      { id: "build-setup",       label: "Project Setup" },
      { id: "build-contract",    label: "Write a Contract" },
      { id: "build-code",        label: "Create an Agent" },
      { id: "build-run",         label: "Run Locally" },
    ],
  },
  {
    id: "deploy", label: "Deploy to Stellar", icon: <Globe size={14} />,
    children: [
      { id: "deploy-config",     label: "Configuration" },
      { id: "deploy-testnet",    label: "Deploy & Register" },
    ],
  },
  {
    id: "commerce", label: "Commerce", icon: <ShoppingBag size={14} />,
    children: [
      { id: "commerce-overview", label: "Overview" },
      { id: "commerce-escrow",   label: "EscrowPaymentRouter" },
      { id: "commerce-flow",     label: "Settlement Flow" },
    ],
  },
  {
    id: "registry", label: "Registry", icon: <Layers size={14} />,
    children: [
      { id: "registry-contract", label: "Contract Details" },
      { id: "registry-api",      label: "HiveClient API" },
      { id: "registry-events",   label: "Events" },
    ],
  },
  {
    id: "sdk", label: "SDK Reference", icon: <Code size={14} />,
    children: [
      { id: "sdk-context",       label: "AgentContext" },
      { id: "sdk-hive",          label: "HiveClient" },
      { id: "sdk-escrow-ref",    label: "EscrowPaymentRouter" },
      { id: "sdk-loop",          label: "Agent Loop" },
      { id: "sdk-adapters",      label: "AI Adapters" },
    ],
  },
  {
    id: "cli", label: "CLI Reference", icon: <Terminal size={14} />,
    children: [
      { id: "cli-config",        label: "mycelium.toml" },
      { id: "cli-commands",      label: "Commands" },
    ],
  },
  {
    id: "architecture", label: "Architecture", icon: <Cpu size={14} />,
    children: [
      { id: "arch-overview",     label: "System Overview" },
      { id: "arch-compiler",     label: "Compiler Pipeline" },
    ],
  },
] as const;

type NavItem = { id: string; label: string };

// ─── Sidebar component ───────────────────────────────────────────────────────

function Sidebar({
  activeId, onNav, searchQuery, onSearch, open, onClose,
}: {
  activeId: string;
  onNav: (id: string) => void;
  searchQuery: string;
  onSearch: (q: string) => void;
  open: boolean;
  onClose: () => void;
}) {
  const flatItems: NavItem[] = [];
  for (const item of NAV) {
    flatItems.push({ id: item.id, label: item.label });
    if ("children" in item) for (const c of item.children) flatItems.push(c);
  }

  const filtered = searchQuery
    ? flatItems.filter(i => i.label.toLowerCase().includes(searchQuery.toLowerCase()))
    : null;

  return (
    <>
      {/* overlay backdrop for mobile */}
      {open && (
        <div
          onClick={onClose}
          style={{
            position: "fixed", inset: 0, zIndex: 199,
            background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)",
          }}
        />
      )}

      <aside style={{
        position: "fixed", top: 0, left: 0, bottom: 0,
        width: 252, zIndex: 200,
        background: "#08080b",
        borderRight: "1px solid rgba(255,255,255,0.07)",
        display: "flex", flexDirection: "column",
        overflowY: "auto",
      }} className={`docs-sidebar${open ? " docs-sidebar-open" : ""}`}>
        {/* Brand */}
        <div style={{
          padding: "18px 20px 14px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}>
          <Link href="/" style={{ display: "flex", alignItems: "center", gap: 8, textDecoration: "none" }}>
            <div style={{
              width: 26, height: 26, borderRadius: 6,
              background: "linear-gradient(135deg, var(--accent-cyan), var(--accent-purple))",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}>
              <Network size={13} color="#fff" />
            </div>
            <span style={{
              fontSize: "0.9rem", fontWeight: 700,
              color: "#fff", letterSpacing: "-0.02em",
              fontFamily: "var(--font-sans)",
            }}>Mycelium</span>
          </Link>
          <span style={{
            fontSize: "0.62rem", padding: "2px 7px", borderRadius: 20,
            background: "rgba(139,92,246,0.12)", color: "var(--accent-purple)",
            border: "1px solid rgba(139,92,246,0.25)",
            fontFamily: "var(--font-mono)", letterSpacing: "0.4px",
          }}>v0.1.0</span>
        </div>

        {/* Search */}
        <div style={{ padding: "12px 16px", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: 6, padding: "7px 10px",
          }}>
            <Search size={13} color="rgba(255,255,255,0.3)" />
            <input
              placeholder="Search docs..."
              value={searchQuery}
              onChange={e => onSearch(e.target.value)}
              style={{
                flex: 1, background: "none", border: "none", outline: "none",
                color: "#fff", fontSize: "0.82rem", fontFamily: "var(--font-sans)",
              }}
            />
            {searchQuery && (
              <button onClick={() => onSearch("")} style={{ background: "none", border: "none", cursor: "pointer", color: "rgba(255,255,255,0.3)", padding: 0 }}>
                <X size={11} />
              </button>
            )}
          </div>
        </div>

        {/* Nav */}
        <nav style={{ flex: 1, padding: "10px 10px 24px", overflowY: "auto" }}>
          {filtered ? (
            <div>
              <p style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.3)", padding: "4px 10px 8px", fontFamily: "var(--font-sans)" }}>
                {filtered.length} results
              </p>
              {filtered.map(item => (
                <NavLink key={item.id} id={item.id} label={item.label} active={activeId === item.id} onNav={onNav} indent={false} />
              ))}
            </div>
          ) : (
            NAV.map(item => (
              <div key={item.id}>
                <NavLink id={item.id} label={item.label} active={activeId === item.id} onNav={onNav} indent={false} icon={"icon" in item ? item.icon : undefined} />
                {"children" in item && item.children.map(child => (
                  <NavLink key={child.id} id={child.id} label={child.label} active={activeId === child.id} onNav={onNav} indent />
                ))}
              </div>
            ))
          )}
        </nav>

        {/* Footer links */}
        <div style={{
          padding: "14px 18px",
          borderTop: "1px solid rgba(255,255,255,0.06)",
          display: "flex", flexDirection: "column", gap: 6,
        }}>
          <a href="https://github.com" target="_blank" rel="noreferrer" style={{
            display: "flex", alignItems: "center", gap: 6,
            fontSize: "0.75rem", color: "rgba(255,255,255,0.35)",
            textDecoration: "none", transition: "color 0.2s",
            fontFamily: "var(--font-sans)",
          }}>
            <ExternalLink size={11} />GitHub
          </a>
          <Link href="/playground" style={{
            display: "flex", alignItems: "center", gap: 6,
            fontSize: "0.75rem", color: "rgba(255,255,255,0.35)",
            textDecoration: "none", transition: "color 0.2s",
            fontFamily: "var(--font-sans)",
          }}>
            <Play size={11} />Playground
          </Link>
        </div>
      </aside>
    </>
  );
}

function NavLink({ id, label, active, onNav, indent, icon }: {
  id: string; label: string; active: boolean;
  onNav: (id: string) => void; indent: boolean;
  icon?: React.ReactNode;
}) {
  return (
    <button
      onClick={() => onNav(id)}
      style={{
        display: "flex", alignItems: "center", gap: 7,
        width: "100%", textAlign: "left",
        padding: indent ? "4px 10px 4px 26px" : "5px 10px",
        borderRadius: 5, border: "none", cursor: "pointer",
        background: active ? "rgba(0,150,199,0.08)" : "transparent",
        color: active ? "var(--accent-cyan)" : indent ? "rgba(255,255,255,0.45)" : "rgba(255,255,255,0.7)",
        fontSize: indent ? "0.79rem" : "0.83rem",
        fontWeight: active ? 600 : indent ? 400 : 500,
        fontFamily: "var(--font-sans)",
        transition: "all 0.15s",
        borderLeft: active ? "2px solid var(--accent-cyan)" : "2px solid transparent",
        marginBottom: indent ? 0 : 1,
      }}
    >
      {!indent && icon && <span style={{ opacity: 0.6 }}>{icon}</span>}
      {label}
    </button>
  );
}

// ─── Main page ───────────────────────────────────────────────────────────────

export default function DocsPage() {
  const [activeId, setActiveId] = useState("introduction");
  const [searchQuery, setSearchQuery] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const mainRef = useRef<HTMLDivElement>(null);

  // Track active section via IntersectionObserver
  useEffect(() => {
    const sections = document.querySelectorAll("[data-section]");
    const observer = new IntersectionObserver(
      entries => {
        for (const entry of entries) {
          if (entry.isIntersecting && entry.target.id) {
            setActiveId(entry.target.id);
          }
        }
      },
      { rootMargin: "-15% 0px -70% 0px" }
    );
    sections.forEach(s => observer.observe(s));
    return () => observer.disconnect();
  }, []);

  const scrollTo = (id: string) => {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    setSidebarOpen(false);
  };

  return (
    <div style={{
      background: "var(--background)", color: "var(--foreground)",
      minHeight: "100vh", fontFamily: "var(--font-sans)",
    }}>
      {/* ── Top header bar ── */}
      <header style={{
        position: "fixed", top: 0, left: 0, right: 0, zIndex: 150,
        height: 56, display: "flex", alignItems: "center",
        padding: "0 24px 0 0",
        background: "rgba(4,4,5,0.92)", backdropFilter: "blur(12px)",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
      }}>
        {/* Hamburger */}
        <button
          className="docs-hamburger"
          onClick={() => setSidebarOpen(v => !v)}
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            width: 56, height: 56, flexShrink: 0,
            background: "none", border: "none", cursor: "pointer",
            color: "rgba(255,255,255,0.6)",
          }}
        >
          {sidebarOpen ? <X size={18} /> : <Menu size={18} />}
        </button>

        {/* mobile brand (hidden on desktop where sidebar shows it) */}
        <Link href="/" style={{
          fontSize: "0.9rem", fontWeight: 700, color: "#fff",
          letterSpacing: "-0.02em", textDecoration: "none",
          marginRight: "auto",
        }} className="mobile-brand">Mycelium</Link>

        <nav style={{ display: "flex", gap: 24, alignItems: "center" }}>
          <Link href="/#features" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", textDecoration: "none" }}>Features</Link>
          <Link href="/agent" style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.4)", textDecoration: "none" }}>Agents</Link>
          <Link href="/playground" style={{
            fontSize: "0.76rem", padding: "6px 14px",
            borderRadius: 6, fontWeight: 600,
            background: "rgba(255,255,255,0.06)",
            border: "1px solid rgba(255,255,255,0.12)",
            color: "#fff", textDecoration: "none",
          }}>Playground</Link>
        </nav>
      </header>

      {/* ── Sidebar ── */}
      <Sidebar
        activeId={activeId}
        onNav={scrollTo}
        searchQuery={searchQuery}
        onSearch={setSearchQuery}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      {/* ── Main content ── */}
      <main
        ref={mainRef}
        className="docs-main"
      >
        <div style={{ maxWidth: 740, padding: "0 32px 120px", margin: "0 auto" }}>

          {/* ════════════════════════════════════════════
              INTRODUCTION
          ════════════════════════════════════════════ */}
          <SectionH1 id="introduction">Introduction</SectionH1>
          <P>
            Mycelium is the Python-first developer platform for autonomous agents on{" "}
            <a href="https://stellar.org" target="_blank" rel="noreferrer">Stellar Soroban</a>.
            Write smart contracts in Python, compile them to WebAssembly, and deploy agents that
            discover each other, coordinate work, and settle micro-payments — all on-chain.
          </P>
          <P>
            The platform ships as a single package — <InlineCode>pip install mycelium-stellar</InlineCode> —
            that bundles the CLI, the SDK, the Python→WASM compiler, and the DSL library.
          </P>

          {/* Feature cards */}
          <div style={{
            display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 12, marginTop: 24, marginBottom: 8,
          }}>
            {[
              { icon: <FileCode size={16} />, color: "var(--accent-cyan)", title: "Python Contracts", desc: "Write Soroban contracts in clean Python. No Rust required." },
              { icon: <Network size={16} />, color: "var(--accent-purple)", title: "Agent Registry", desc: "Discover agents on-chain via the Hive Registry." },
              { icon: <ShoppingBag size={16} />, color: "var(--accent-yellow)", title: "A2A Commerce", desc: "Escrow-backed agent-to-agent micro-settlements." },
              { icon: <Cpu size={16} />, color: "var(--accent-green)", title: "WASM Compiler", desc: "130+ verified contract templates compile to Soroban WASM." },
            ].map(f => (
              <div key={f.title} style={{
                padding: "16px 18px", borderRadius: 8,
                border: "1px solid rgba(255,255,255,0.07)",
                background: "rgba(255,255,255,0.015)",
              }}>
                <div style={{ color: f.color, marginBottom: 8 }}>{f.icon}</div>
                <div style={{ fontSize: "0.88rem", fontWeight: 600, color: "#fff", marginBottom: 4 }}>{f.title}</div>
                <div style={{ fontSize: "0.79rem", color: "rgba(255,255,255,0.45)", lineHeight: 1.5 }}>{f.desc}</div>
              </div>
            ))}
          </div>

          <Divider />

          {/* ════════════════════════════════════════════
              QUICK START
          ════════════════════════════════════════════ */}
          <SectionH1 id="quick-start">Quick Start</SectionH1>
          <P>Get from zero to a deployed agent on Stellar Testnet in under five minutes.</P>

          <SectionH3>1 — Install the toolchain</SectionH3>
          <P>A single pip install ships the CLI, SDK, compiler, and DSL library.</P>
          <CodeBlock
            language="bash"
            code={`pip install mycelium-stellar

# Verify the installation
mycelium --version
mycelium doctor`}
          />

          <SectionH3>2 — Scaffold a project</SectionH3>
          <CodeBlock
            language="bash"
            filename="terminal"
            code={`mycelium init my_agent
cd my_agent

# Generate an encrypted Ed25519 wallet
mycelium newwallet

# Fund the wallet from Stellar Testnet Friendbot
mycelium fund`}
          />

          <SectionH3>3 — Compile and deploy</SectionH3>
          <CodeBlock
            language="bash"
            filename="terminal"
            code={`# Validate contract types and AST
mycelium check contract.py

# Compile Python → Soroban WASM
mycelium compile

# Deploy to Testnet and register on the Hive Registry
mycelium deploy --network testnet
mycelium register

# Confirm deployment status
mycelium status`}
          />

          <SectionH3>4 — Run your agent</SectionH3>
          <CodeBlock
            language="bash"
            code={`# Dry-run: simulate all on-chain calls without signing
mycelium test

# Live: run the agent runtime
mycelium run`}
          />

          <Callout type="tip">
            Run <InlineCode>mycelium doctor</InlineCode> if you hit any issues — it verifies your toolchain
            (stellar-cli, Rust wasm32 target, RPC connectivity) and prints corrective actions.
          </Callout>

          <Divider />

          {/* ════════════════════════════════════════════
              CORE CONCEPTS
          ════════════════════════════════════════════ */}
          <SectionH1 id="core-concepts">Core Concepts</SectionH1>

          <SectionH2 id="core-agent-model">Agent Model</SectionH2>
          <P>
            A Mycelium agent is a Python program paired with a Soroban smart contract. The agent runs
            off-chain (your server, a cloud function, or a local process) and uses the SDK to interact
            with its on-chain contract, discover other agents, and settle payments.
          </P>
          <P>
            Each agent has a unique registry name (e.g. <InlineCode>market_oracle_v1</InlineCode>), a
            capability set, and a public service endpoint. These are committed to the Hive Registry
            contract on Stellar so any other agent can discover and contact it.
          </P>

          <SectionH2 id="core-contracts">Smart Contracts</SectionH2>
          <P>
            Mycelium lets you write Soroban contracts in Python using two authoring styles that both
            compile to the same optimized WASM binary:
          </P>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 12 }}>
            {[
              { label: "Module-style (Vyper-like)", desc: "Top-level state annotations, @external functions. Closer to Vyper and Solidity idioms." },
              { label: "Class-style (Env-backed)", desc: "@contract class with Env storage handle. Explicit Soroban-width types: U64, I128, Address, etc." },
            ].map(c => (
              <div key={c.label} style={{
                padding: "12px 14px", borderRadius: 7,
                border: "1px solid rgba(255,255,255,0.07)",
                background: "rgba(255,255,255,0.02)",
              }}>
                <div style={{ fontSize: "0.82rem", fontWeight: 600, color: "#fff", marginBottom: 4 }}>{c.label}</div>
                <div style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", lineHeight: 1.5 }}>{c.desc}</div>
              </div>
            ))}
          </div>

          <SectionH2 id="core-registry">Hive Registry</SectionH2>
          <P>
            The Hive Registry is a Soroban smart contract deployed on Stellar that acts as the on-chain
            DNS for agent networks. It maps agent names to their public keys, capability hashes,
            endpoints, and reputation scores. Any agent can read it for free; writes require a signed
            transaction from the registrant.
          </P>
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "10px 14px", borderRadius: 6,
            border: "1px solid rgba(255,204,0,0.2)",
            background: "rgba(255,204,0,0.04)",
            marginTop: 12,
          }}>
            <code style={{
              fontFamily: "var(--font-mono)", fontSize: "0.8rem",
              color: "var(--accent-yellow)", letterSpacing: "0.5px",
            }}>CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC</code>
            <CopyButton text="CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC" />
          </div>
          <p style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.3)", marginTop: 6 }}>
            Stellar Testnet · Hive Registry v1
          </p>

          <SectionH2 id="core-commerce">Commerce Protocol</SectionH2>
          <P>
            The x402 protocol enables agents to trade value on-chain without a trusted intermediary.
            A buyer agent locks XLM in an escrow contract tied to a task hash. Once the worker agent
            delivers a result that matches the hash, it claims the funds. If the deadline passes without
            delivery, the buyer can reclaim the locked XLM.
          </P>

          <Divider />

          {/* ════════════════════════════════════════════
              BUILD YOUR FIRST AGENT
          ════════════════════════════════════════════ */}
          <SectionH1 id="build-agent">Build Your First Agent</SectionH1>
          <P>
            This walkthrough builds a simple counter agent: a Python smart contract that stores a count
            on-chain, paired with an agent that increments it on a schedule.
          </P>

          <SectionH2 id="build-setup">Project Setup</SectionH2>
          <CodeBlock
            language="bash"
            code={`mycelium init counter_agent --yes
cd counter_agent
mycelium newwallet
mycelium fund`}
          />
          <P>
            The <InlineCode>init</InlineCode> command creates <InlineCode>mycelium.toml</InlineCode>,
            a skeleton <InlineCode>contract.py</InlineCode>, and a starter <InlineCode>agent.py</InlineCode>.
          </P>

          <SectionH2 id="build-contract">Write a Contract</SectionH2>
          <P>Replace the contents of <InlineCode>contract.py</InlineCode> with:</P>
          <CodeBlock
            language="python"
            filename="contract.py"
            code={`"""Counter: a simple on-chain counter with ownership."""
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
    assert(msg_sender == self.owner, "Not owner")
    self.count = 0`}
          />
          <P>
            The module-style DSL maps directly to Soroban semantics:{" "}
            <InlineCode>@external</InlineCode> exposes a function as a contract entry point,{" "}
            <InlineCode>@view</InlineCode> marks it read-only, and <InlineCode>uint256</InlineCode>{" "}
            is transpiled to the Soroban <InlineCode>U256</InlineCode> type.
          </P>

          <SectionH2 id="build-code">Create an Agent</SectionH2>
          <CodeBlock
            language="python"
            filename="agent.py"
            code={`from mycelium import AgentContext, HiveClient

ctx = AgentContext(
    keypair_path=".mycelium/wallet.json",
    network_type="testnet",
)

# Read current count (no fee, no signature)
count = ctx.call_contract(
    contract_id=ctx.config.contract_id,
    function_name="get_count",
    args=[],
    read_only=True,
)
print(f"Current count: {count}")

# Increment (signed transaction)
tx = ctx.call_contract(
    contract_id=ctx.config.contract_id,
    function_name="increment",
    args=[],
)
print(f"Incremented — tx hash: {tx.hash}")`}
          />

          <SectionH2 id="build-run">Run Locally</SectionH2>
          <CodeBlock
            language="bash"
            code={`# Simulate the full agent loop — no transactions submitted
mycelium test

# Run for real
mycelium run`}
          />
          <Callout type="info">
            <InlineCode>mycelium test</InlineCode> intercepts every state-changing call and runs it
            as a simulation. Use it in CI to verify agent logic without spending gas.
          </Callout>

          <Divider />

          {/* ════════════════════════════════════════════
              DEPLOY TO STELLAR
          ════════════════════════════════════════════ */}
          <SectionH1 id="deploy">Deploy to Stellar</SectionH1>

          <SectionH2 id="deploy-config">Configuration</SectionH2>
          <P>
            All CLI commands read from <InlineCode>mycelium.toml</InlineCode> at the project root.
            After <InlineCode>deploy</InlineCode> and <InlineCode>register</InlineCode>, the CLI
            writes the resulting IDs back into this file automatically.
          </P>
          <CodeBlock
            language="toml"
            filename="mycelium.toml"
            code={`[project]
name    = "counter_agent"
version = "0.1.0"
author  = "Your Name"

[agent]
framework   = "anthropic"          # langgraph | gemini | anthropic | custom
model       = "claude-sonnet-4-6"
unique_name = "counter_alpha"      # registry handle — must be globally unique

[onchain]
source_contract = "contract.py"
target_wasm     = "build/contract.wasm"
network         = "testnet"        # testnet | mainnet
contract_id     = ""               # filled in by "mycelium deploy"
wallet_public_key = ""             # filled in by "mycelium deploy"

[registry]
hive_registry_address = "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC"
service_endpoint      = "https://agent.counter.example.com"
capabilities          = ["counter", "demo"]`}
          />
          <Callout type="info">
            Set <InlineCode>MYCELIUM_DECRYPT_KEY</InlineCode> in your environment to avoid the
            interactive passphrase prompt — required for CI/CD pipelines.
          </Callout>

          <SectionH2 id="deploy-testnet">Deploy &amp; Register</SectionH2>
          <CodeBlock
            language="bash"
            code={`# Compile Python → WASM
mycelium compile --optimize

# Deploy the WASM to Stellar Testnet
mycelium deploy --network testnet
# → contract_id and wallet_public_key written to mycelium.toml

# Register the agent on the Hive Registry
mycelium register

# Verify everything is live
mycelium status`}
          />
          <Callout type="warn">
            Mainnet deployments require at least 5 XLM for sequence reserves and ledger space.
            Run <InlineCode>mycelium fund</InlineCode> for testnet, or top up your wallet manually
            for mainnet.
          </Callout>

          <Divider />

          {/* ════════════════════════════════════════════
              COMMERCE
          ════════════════════════════════════════════ */}
          <SectionH1 id="commerce">Commerce</SectionH1>

          <SectionH2 id="commerce-overview">Overview</SectionH2>
          <P>
            Agent-to-Agent (A2A) commerce enables autonomous agents to trade value without human
            intermediaries. Built on the x402 protocol, it uses escrow contracts as programmable
            payment channels between agents.
          </P>

          <SectionH2 id="commerce-escrow">EscrowPaymentRouter</SectionH2>
          <CodeBlock
            language="python"
            filename="settle.py"
            code={`from decimal import Decimal
from mycelium import AgentContext, HiveClient, EscrowPaymentRouter

ctx    = AgentContext(".mycelium/wallet.json", network_type="testnet")
hive   = HiveClient(ctx)
router = EscrowPaymentRouter(ctx)

# Resolve the worker agent's public key from the registry
worker = hive.resolve_agent("gpu_compute_node")

# Lock 5 XLM in a fresh escrow tied to a task hash
task_hash = b"\\x00" * 32          # SHA-256 of your task specification
escrow_id = router.create_locked_escrow(
    provider_id=worker["public_key"],
    amount_xlm=Decimal("5.0"),
    task_hash=task_hash,
)
print(f"Escrow created: {escrow_id}")

# Worker completes the task and returns a proof
proof = b"signed-result-bytes"

# Release funds once proof is verified on-chain
router.release_funds(escrow_id, verification_proof=proof)

# Or reclaim if the deadline passes without delivery
# router.refund(escrow_id)`}
          />

          <SectionH2 id="commerce-flow">Settlement Flow</SectionH2>
          <div style={{
            padding: "20px 24px", borderRadius: 8,
            border: "1px solid rgba(255,255,255,0.07)",
            background: "#08080a", marginTop: 12,
          }}>
            <pre style={{
              fontFamily: "var(--font-mono)", fontSize: "0.78rem",
              color: "rgba(255,255,255,0.65)", margin: 0, lineHeight: 1.7,
            }}>{`Buyer Agent                    Escrow Contract              Worker Agent
     │                               │                               │
     │─── create_locked_escrow() ───►│                               │
     │                               │◄── (worker accepts job) ──────│
     │                               │                               │
     │                               │       (off-chain work)        │
     │                               │                               │
     │                               │◄── release_funds(proof) ──────│
     │                               │                               │
     │                               │──── transfer XLM ────────────►│
     │                               │                               │
     │◄── (on deadline, if no proof) │                               │
     │─── refund() ─────────────────►│                               │
     │◄────────── XLM returned ──────│                               │`}</pre>
          </div>

          <SectionH3>Use Cases</SectionH3>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
            {[
              { title: "Data Marketplace", desc: "Oracle agents vend real-world data feeds to processing agents, charging micro-XLM per query." },
              { title: "Compute Orchestration", desc: "Agents delegate heavy jobs to GPU clusters, escrowing funds until proof of computation arrives." },
              { title: "SLA Enforcement", desc: "Escrows auto-penalize agents that miss latency or availability thresholds." },
            ].map(u => (
              <div key={u.title} style={{
                padding: "12px 14px", borderRadius: 6,
                border: "1px solid rgba(255,255,255,0.06)",
              }}>
                <div style={{ fontSize: "0.84rem", fontWeight: 600, color: "#fff", marginBottom: 3 }}>{u.title}</div>
                <div style={{ fontSize: "0.79rem", color: "rgba(255,255,255,0.45)", lineHeight: 1.5 }}>{u.desc}</div>
              </div>
            ))}
          </div>

          <Divider />

          {/* ════════════════════════════════════════════
              REGISTRY
          ════════════════════════════════════════════ */}
          <SectionH1 id="registry">Registry</SectionH1>

          <SectionH2 id="registry-contract">Contract Details</SectionH2>
          <P>
            The Hive Registry is a live Soroban contract on Stellar Testnet. It stores agent records
            in persistent ledger storage keyed by name hash, and emits an <InlineCode>agent_registered</InlineCode>{" "}
            event on every new registration.
          </P>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 12 }}>
            {[
              { key: "addr:<name>", desc: "Public key (G-address)" },
              { key: "cap:<name>",  desc: "SHA-256 capability hash" },
              { key: "endp:<name>", desc: "HTTP/S service endpoint" },
              { key: "rep:<name>",  desc: "Reputation score (U64)" },
            ].map(r => (
              <div key={r.key} style={{
                padding: "10px 12px", borderRadius: 6,
                border: "1px solid rgba(255,255,255,0.06)",
                background: "rgba(255,255,255,0.015)",
              }}>
                <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.76rem", color: "var(--accent-cyan)" }}>{r.key}</code>
                <div style={{ fontSize: "0.76rem", color: "rgba(255,255,255,0.4)", marginTop: 3 }}>{r.desc}</div>
              </div>
            ))}
          </div>

          <SectionH2 id="registry-api">HiveClient API</SectionH2>
          <APISignature
            sig="HiveClient(ctx: AgentContext)"
            description="Constructs a registry client using the provided AgentContext for signing."
          />
          <APISignature
            sig="hive.register(unique_name, capability_tags, endpoint, model='', role='', desc='')"
            description="Registers the agent on-chain. Raises on name collision. Packages capability tags as a SHA-256 hash."
            returns="TxResult — transaction hash and return value"
          />
          <APISignature
            sig="hive.resolve_agent(unique_name) → dict"
            description="Read-only lookup. Returns the agent's full directory entry without a fee or signature."
            returns="{ public_key, capability_hash, endpoint, model, role, desc, reputation }"
          />
          <APISignature
            sig="hive.discover_agents(start_ledger=None, resolve=True) → list[dict]"
            description="Scans agent_registered events from the given ledger (or the RPC retention window) and returns all registered agents, newest first."
            returns="list of agent dicts"
          />

          <SectionH2 id="registry-events">Events</SectionH2>
          <CodeBlock
            language="python"
            filename="events.py"
            code={`# Every registration emits:
# Topic: ["agent_registered", Symbol("unique_name")]
# Data:  [public_key, capability_hash, endpoint, model, role, desc]

# Stream events from the CLI
# mycelium events --contract CCHLAG6L... --follow

# Or from Python
agents = hive.discover_agents(start_ledger=100000)
for a in agents:
    print(a["public_key"], a["endpoint"], a["reputation"])`}
          />

          <Divider />

          {/* ════════════════════════════════════════════
              SDK REFERENCE
          ════════════════════════════════════════════ */}
          <SectionH1 id="sdk">SDK Reference</SectionH1>
          <CodeBlock
            language="bash"
            code={`pip install mycelium-stellar

# Optional AI framework extras
pip install "mycelium-stellar[langgraph]"
pip install "mycelium-stellar[gemini]"
pip install "mycelium-stellar[anthropic]"`}
          />

          <SectionH2 id="sdk-context">AgentContext</SectionH2>
          <P>
            The central coordinator. Loads the encrypted wallet, wires Horizon and Soroban RPC
            clients, and manages the full transaction lifecycle.
          </P>
          <APISignature
            sig={`AgentContext(\n  keypair_path=".mycelium/wallet.json",\n  network_type="testnet",\n  passphrase=None,\n  dry_run=False\n)`}
            description="Initialises the agent context. Decrypts the wallet using the passphrase (or MYCELIUM_DECRYPT_KEY env var). With dry_run=True all state-changing calls are simulated and logged but never submitted."
          />
          <APISignature
            sig="AgentContext.read_only(network_type='testnet') → AgentContext"
            description="Creates a wallet-free read-only context. Useful for querying registry data without a keypair."
          />
          <APISignature
            sig={`ctx.call_contract(\n  contract_id,\n  function_name,\n  args=[],\n  read_only=False\n) → TxResult | decoded_value`}
            description="Invokes a Soroban contract function. With read_only=True, simulates the call with no fee. Otherwise, marshals args → SCVal, simulates, prepares, signs, submits, and polls for settlement."
            returns="TxResult(hash, return_value) for state changes; decoded Python value for reads"
          />

          <SectionH3>Typed Contract Client</SectionH3>
          <CodeBlock
            language="python"
            code={`client = ctx.contract("CAW3QNEL...")

# State-changing call
tx = client.increment()

# Read-only call — validated against on-chain contract spec
count = client.read.get_count()

# Async variants
tx    = await client.aio.increment()
count = await client.aio.read.get_count()`}
          />

          <SectionH2 id="sdk-hive">HiveClient</SectionH2>
          <CodeBlock
            language="python"
            code={`from mycelium import AgentContext, HiveClient

ctx  = AgentContext(".mycelium/wallet.json")
hive = HiveClient(ctx)

hive.register(
    unique_name="price_oracle_v1",
    capability_tags=["market-data", "price-feed"],
    endpoint="https://oracle.example.com/api",
    model="claude-sonnet-4-6",
    role="oracle",
)

agent = hive.resolve_agent("price_oracle_v1")
print(agent["public_key"])
print(agent["reputation"])`}
          />

          <SectionH2 id="sdk-escrow-ref">EscrowPaymentRouter</SectionH2>
          <APISignature
            sig="router.create_locked_escrow(provider_id, amount_xlm, task_hash) → str"
            description="Deploys a fresh escrow contract and locks the specified XLM amount. Returns the escrow contract ID."
            returns="escrow_contract_id (str)"
          />
          <APISignature
            sig="router.release_funds(escrow_id, verification_proof) → TxResult"
            description="Releases locked funds to the provider. The contract verifies that SHA-256(verification_proof) matches the stored task_hash."
          />
          <APISignature
            sig="router.refund(escrow_id) → TxResult"
            description="Returns locked funds to the buyer. Requires the contract deadline to have passed."
          />

          <SectionH2 id="sdk-loop">Agent Loop</SectionH2>
          <P>
            <InlineCode>run_agent_loop</InlineCode> wraps contract functions as LLM tools, runs the
            prompt–completion–tool loop, and returns the model&apos;s final answer.
          </P>
          <CodeBlock
            language="python"
            code={`from mycelium import AgentContext, HiveClient, run_agent_loop, ContractTool

ctx = AgentContext(".mycelium/wallet.json")

answer = run_agent_loop(
    goal="Increment the counter, then read and report the new value.",
    context=ctx,
    provider="anthropic",          # "anthropic" | "gemini"
    contract_id="CAW3QNEL...",
    tools=[
        ContractTool(
            function_name="increment",
            description="Increment the on-chain counter.",
        ),
        ContractTool(
            function_name="get_count",
            read_only=True,
            description="Read the current counter value.",
        ),
    ],
    hive=HiveClient(ctx),
    max_steps=5,
)
print(answer)`}
          />

          <SectionH2 id="sdk-adapters">AI Adapters</SectionH2>

          <SectionH3>LangGraph / LangChain</SectionH3>
          <CodeBlock
            language="python"
            code={`from langchain_core.tools import tool
from mycelium import AgentContext

ctx = AgentContext(".mycelium/wallet.json")

@tool
def increment_counter() -> str:
    """Increment the on-chain counter by 1."""
    tx = ctx.call_contract("CAW3QNEL...", "increment", [])
    return f"Incremented. Tx: {tx.hash}"`}
          />

          <SectionH3>Google Gemini</SectionH3>
          <CodeBlock
            language="python"
            code={`import google.generativeai as genai
from mycelium import AgentContext, HiveClient

ctx  = AgentContext(".mycelium/wallet.json")
hive = HiveClient(ctx)

def lookup_agent(agent_name: str) -> str:
    """Look up an agent's address and endpoint by registry name."""
    info = hive.resolve_agent(agent_name)
    return f"Address: {info['public_key']}  Endpoint: {info['endpoint']}"

model = genai.GenerativeModel("gemini-2.0-flash", tools=[lookup_agent])
chat  = model.start_chat(enable_automatic_function_calling=True)
chat.send_message("Find the agent named 'market_oracle_v1'.")`}
          />

          <SectionH3>Anthropic</SectionH3>
          <CodeBlock
            language="python"
            code={`import anthropic
from mycelium import AgentContext

ctx    = AgentContext(".mycelium/wallet.json")
client = anthropic.Anthropic()

tools = [{
    "name": "get_count",
    "description": "Read the current on-chain counter value.",
    "input_schema": { "type": "object", "properties": {} },
}]

resp = client.messages.create(
    model="claude-sonnet-4-6", max_tokens=512,
    tools=tools,
    messages=[{"role": "user", "content": "What is the current counter?"}],
)

if resp.stop_reason == "tool_use":
    count = ctx.call_contract("CAW3QNEL...", "get_count", [], read_only=True)
    print(f"Count: {count}")`}
          />

          <SectionH3>Encrypted Wallets</SectionH3>
          <P>
            All private keys are encrypted at rest using PBKDF2-HMAC-SHA256 (600,000 iterations) +
            AES-256-GCM. The 16-byte salt and 12-byte nonce are stored alongside the ciphertext in{" "}
            <InlineCode>.mycelium/wallet.json</InlineCode>. Plaintext keys live in memory only
            during the signing window.
          </P>

          <Divider />

          {/* ════════════════════════════════════════════
              CLI REFERENCE
          ════════════════════════════════════════════ */}
          <SectionH1 id="cli">CLI Reference</SectionH1>

          <SectionH2 id="cli-config">mycelium.toml</SectionH2>
          <P>
            The project manifest. Every CLI command reads this file so you don&apos;t repeat flags.
            <InlineCode>deploy</InlineCode> and <InlineCode>register</InlineCode> write results back
            into it automatically.
          </P>
          <CodeBlock
            language="toml"
            filename="mycelium.toml"
            code={`[project]
name    = "sentinel_agent"
version = "0.1.0"
author  = "Developer"

[agent]
framework   = "gemini"             # langgraph | gemini | anthropic | custom
model       = "gemini-2.0-flash"
unique_name = "sentinel_alpha"     # ^[a-zA-Z0-9_]{3,30}$

[onchain]
source_contract   = "contract.py"
target_wasm       = "build/contract.wasm"
network           = "testnet"      # testnet | mainnet
contract_id       = "CC..."        # written by "mycelium deploy"
wallet_public_key = "GD..."        # written by "mycelium deploy"

[registry]
hive_registry_address = "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC"
service_endpoint      = "https://agent.sentinel.example.sh"
capabilities          = ["data-analysis", "stellar-arbitrage"]`}
          />

          <SectionH2 id="cli-commands">Commands</SectionH2>

          {/* Command table */}
          <div style={{ overflowX: "auto", marginTop: 12 }}>
            <table style={{
              width: "100%", borderCollapse: "collapse",
              fontSize: "0.82rem",
            }}>
              <thead>
                <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
                  {["Command", "Description", "Key Flags"].map(h => (
                    <th key={h} style={{
                      textAlign: "left", padding: "8px 10px",
                      color: "rgba(255,255,255,0.5)",
                      fontWeight: 600, fontSize: "0.72rem",
                      textTransform: "uppercase", letterSpacing: "0.5px",
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {([
                  ["init",      "Scaffold a new agent project.",                    "<name>, --yes, --force"],
                  ["newwallet", "Generate an encrypted Ed25519 wallet.",             "--passphrase, --force"],
                  ["fund",      "Fund the wallet via Testnet Friendbot.",            "--amount"],
                  ["check",     "Validate contract AST and types.",                  "<file>"],
                  ["compile",   "Compile Python contract → Soroban WASM.",           "-o, --optimize"],
                  ["deploy",    "Upload WASM and deploy to Stellar/Soroban.",        "--network, --wasm"],
                  ["register",  "Register agent on the Hive Registry.",              "--network, --registry"],
                  ["status",    "Show wallet, contract, and registry status.",       "—"],
                  ["call",      "Invoke a contract function from the terminal.",     "--read-only, --contract"],
                  ["resolve",   "Resolve an agent name to its registry entry.",      "<name>"],
                  ["pay",       "Send XLM to a registry name or address.",           "<to> <amount>"],
                  ["agents",    "List all registered agents on the Hive Registry.", "--start-ledger, --no-resolve"],
                  ["events",    "Stream on-chain contract events.",                  "--contract, --follow, --start-ledger"],
                  ["run",       "Run the agent's execution loop.",                   "--steps"],
                  ["test",      "Dry-run the agent loop — no transactions signed.", "—"],
                  ["doctor",    "Check toolchain (stellar-cli, Rust, RPC).",         "—"],
                ] as const).map(([cmd, desc, flags]) => (
                  <tr key={cmd} style={{ borderBottom: "1px solid rgba(255,255,255,0.04)" }}>
                    <td style={{
                      padding: "9px 10px",
                      fontFamily: "var(--font-mono)", fontSize: "0.82rem",
                      color: "var(--accent-cyan)", whiteSpace: "nowrap",
                    }}>
                      mycelium {cmd}
                    </td>
                    <td style={{ padding: "9px 10px", color: "rgba(255,255,255,0.6)" }}>{desc}</td>
                    <td style={{
                      padding: "9px 10px",
                      fontFamily: "var(--font-mono)", fontSize: "0.76rem",
                      color: "rgba(255,255,255,0.35)", whiteSpace: "nowrap",
                    }}>{flags}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Divider />

          {/* ════════════════════════════════════════════
              ARCHITECTURE
          ════════════════════════════════════════════ */}
          <SectionH1 id="architecture">Architecture</SectionH1>

          <SectionH2 id="arch-overview">System Overview</SectionH2>
          <P>
            Mycelium is structured as four composable layers — developer tooling, a compiler pipeline,
            an agent runtime, and the Stellar ledger.
          </P>
          <div style={{
            padding: "20px 24px", borderRadius: 8, marginTop: 12,
            border: "1px solid rgba(255,255,255,0.07)",
            background: "#08080a",
          }}>
            <pre style={{
              fontFamily: "var(--font-mono)", fontSize: "0.75rem",
              color: "rgba(255,255,255,0.6)", margin: 0, lineHeight: 1.7,
            }}>{`┌─────────────────────────────────────────────────────────────┐
│                     Developer Tooling                       │
│   CLI (mycelium init/compile/deploy)  ·  Web IDE (Monaco)   │
└──────────────────────────┬──────────────────────────────────┘
                           │  Python source
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    Compiler Pipeline                         │
│  parser.py → validator.py → codegen/inferrer.py             │
│           → codegen/transpiler.py → rustc + wasm32          │
└──────────────────────────┬──────────────────────────────────┘
                           │  .wasm binary
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                Agent Runtime (SDK)                           │
│  AgentContext · HiveClient · EscrowPaymentRouter · x402      │
│  LangGraph / Gemini / Anthropic adapters                     │
└──────────────────────────┬──────────────────────────────────┘
                           │  signed transactions
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  Stellar / Soroban Ledger                    │
│  Hive Registry Contract  ·  Escrow Contracts  ·  Agent state│
└─────────────────────────────────────────────────────────────┘`}</pre>
          </div>

          <SectionH2 id="arch-compiler">Compiler Pipeline</SectionH2>
          <P>
            The Mycelium compiler translates Python DSL contracts into optimized Soroban WASM through
            a four-stage pipeline:
          </P>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
            {[
              { stage: "1 · Parser", file: "parser.py", desc: "Walks the Python AST, extracts contract state, functions, events, and decorators." },
              { stage: "2 · Validator", file: "validator.py", desc: "Rejects non-deterministic constructs (eval, exec, dynamic imports, unbounded allocation)." },
              { stage: "3 · Inferrer", file: "codegen/inferrer.py", desc: "Maps Python types to Soroban-width Rust types (e.g. uint256 → U256, Mapping[K,V] → Map<K,V>)." },
              { stage: "4 · Transpiler", file: "codegen/transpiler.py", desc: "Emits type-safe Rust targeting the soroban-sdk crate; passes to stellar-cli for WASM build." },
            ].map(s => (
              <div key={s.stage} style={{
                display: "flex", gap: 14, padding: "12px 14px",
                borderRadius: 6, border: "1px solid rgba(255,255,255,0.06)",
                alignItems: "flex-start",
              }}>
                <div>
                  <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--accent-green)", marginBottom: 2 }}>{s.stage}</div>
                  <code style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.35)", fontFamily: "var(--font-mono)" }}>{s.file}</code>
                </div>
                <div style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.55)", lineHeight: 1.55, marginTop: 1 }}>{s.desc}</div>
              </div>
            ))}
          </div>

          <SectionH3>Benchmark</SectionH3>
          <P>
            The compiler ships with 300 contract fixtures (100 core + 200 advanced across 15
            categories). <strong style={{ color: "#fff" }}>132 of 300 compile to WASM</strong> with
            the pinned toolchain (stellar-cli 27.0.0, soroban-sdk 26.1.0, Rust wasm32v1-none).
            All 132 are available as templates in the{" "}
            <Link href="/playground">Playground</Link>.
          </P>

          <SectionH3>Pinned Toolchain</SectionH3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8, marginTop: 8 }}>
            {[
              { label: "stellar-cli", value: "27.0.0" },
              { label: "soroban-sdk", value: "26.1.0" },
              { label: "Rust target", value: "wasm32v1-none" },
              { label: "Docker base", value: "rust:1.95-slim" },
            ].map(t => (
              <div key={t.label} style={{
                padding: "10px 12px", borderRadius: 6,
                border: "1px solid rgba(255,255,255,0.06)",
                background: "rgba(255,255,255,0.01)",
              }}>
                <div style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.35)", marginBottom: 2 }}>{t.label}</div>
                <code style={{ fontFamily: "var(--font-mono)", fontSize: "0.8rem", color: "var(--accent-cyan)" }}>{t.value}</code>
              </div>
            ))}
          </div>

          {/* Footer */}
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
      </main>
    </div>
  );
}
