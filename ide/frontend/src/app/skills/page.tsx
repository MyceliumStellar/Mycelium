"use client";

import React, { useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  Code2,
  Copy,
  Check,
  Cpu,
  Layers,
  Shield,
  Workflow,
  ArrowRight,
  Terminal,
  FileCode,
  Play,
  HelpCircle,
  CheckCircle2,
  ExternalLink,
  BookOpen,
  Settings,
  Terminal as CliIcon
} from "lucide-react";

export default function SkillsPage() {
  const [activeTab, setActiveTab] = useState<"agent" | "job">("agent");
  const [copiedText, setCopiedText] = useState<string | null>(null);

  const handleCopy = (text: string, label: string) => {
    navigator.clipboard.writeText(text);
    setCopiedText(label);
    setTimeout(() => setCopiedText(null), 2000);
  };

  const agentSkillPath = "https://github.com/MyceliumStellar/Mycelium/blob/main/.agents/skills/agent_skill/SKILL.md";
  const jobSkillPath = "https://github.com/MyceliumStellar/Mycelium/blob/main/.agents/skills/job_skill/SKILL.md";

  const agentSkillMd = `---
name: agent-skill
description: Setup Mycelium environment, install SDK/CLI, configure wallets, resolve network congestion, and scaffold autonomous agent loops.
---

# Mycelium Agent Skill (v0.4.0)

This skill guides a code-execution agent (like Claude Code, Antigravity, or other IDE-bound assistants) to setup the Mycelium autonomous agent runtime on Stellar Testnet, resolve developer environmental errors, and program stateless loop integrations.

## 🛠️ Step-by-Step Installation & Setup

### 1. Environment & Virtual Environment
Always isolate python packages to avoid dependency conflicts.
\`\`\`bash
python3 -m venv venv
source venv/bin/activate
\`\`\`
* Troubleshooting: Ensure Python 3.10+ is installed on the host OS. On Linux/Debian:
  $ sudo apt-get update && sudo apt-get install -y python3-venv python3-pip

### 2. Installing the Metapackage
\`\`\`bash
pip install --upgrade pip
pip install mycelium-stellar==0.4.0
\`\`\`
* Troubleshooting: Ensure build-essential package compiler is installed. On Debian/Ubuntu:
  $ sudo apt-get install build-essential python3-dev libssl-dev libffi-dev

### 3. CLI Initialization
\`\`\`bash
mycelium init
\`\`\`

## 🔑 Wallet Scaffolding & Key Management

### 1. Generating Wallet
Generate a new encrypted keypair:
\`\`\`bash
mycelium newwallet
\`\`\`
You will be prompted to choose a password.
* Troubleshooting: Set MYCELIUM_PASSPHRASE environment variable before running. The CLI automatically reads this variable to skip interactive prompts:
  $ export MYCELIUM_PASSPHRASE="your_strong_passphrase_here"
  $ mycelium newwallet

### 2. Requesting Testnet Funds (Friendbot)
The wallet needs native XLM to pay for transaction gas fees on Soroban:
\`\`\`bash
mycelium fund
\`\`\`
* Troubleshooting: If the CLI fails with a connection error or 429 Too Many Requests, fetch your public key address using 'mycelium status' and fund it manually by hitting the Friendbot API directly:
  $ PUBLIC_KEY=$(mycelium status | grep "Public Key" | awk '{print $NF}')
  $ curl -X GET "https://friendbot.stellar.org/?addr=\${PUBLIC_KEY}"

## 🤖 Programming the Autonomous Agent Loop

Use this exact programmatic framework to build a Python script (agent_loop.py) that delegates tasks to LLMs (Gemini/Anthropic) while executing on-chain transactions via tool calling:

\`\`\`python
import os
import sys
from mycelium import AgentContext, HiveClient, run_agent_loop, ContractTool

# Retrieve passphrase from environment or fail early
passphrase = os.getenv("MYCELIUM_PASSPHRASE")
if not passphrase:
    print("[Error] MYCELIUM_PASSPHRASE environment variable is required.", file=sys.stderr)
    sys.exit(1)

try:
    # 1. Initialize encrypted context (decrypted only in memory)
    context = AgentContext(".mycelium/wallet.json", passphrase=passphrase)
    hive = HiveClient(context)
    print(f"[Success] Loaded wallet address: {context.keypair.public_key}")
except Exception as e:
    print(f"[Fatal] Failed to decrypt wallet: {e}", file=sys.stderr)
    sys.exit(1)

# 2. Define the contract tools for the agent
counter_contract = "CDASJ42STDU42QXDXH3KRFNQWBURB54XPXV2WBXHWGPBA2BNAI5EYULO"

tools = [
    ContractTool(
        function_name="increment",
        description="Calls the increment state function on the contract. Requires transaction gas fee.",
        contract_id=counter_contract
    ),
    ContractTool(
        function_name="get_count",
        read_only=True,
        description="Reads the current total count state. Read-only.",
        contract_id=counter_contract
    )
]

# 3. Execute the agent loop
try:
    print("Starting agent execution loop...")
    final_output = run_agent_loop(
        goal="Increment the counter, check if it succeeded, and report the new total.",
        context=context,
        provider="gemini", # Supports "gemini", "anthropic", "openai", "ollama"
        tools=tools,
        hive=hive,
        max_steps=5
    )
    print(f"\\n[Agent Completed]\\n{final_output}")
except Exception as loop_error:
    print(f"[Loop Exception] Agent failed during execution: {loop_error}", file=sys.stderr)
    sys.exit(1)
\`\`\`

## ⚠️ Crucial Edge Cases & Troubleshooting

### 1. Network Congestion & Sequence Number Mismatches (txBAD_SEQ)
When multiple agent loops submit transactions rapidly, sequence numbers can fall out of sync:
* SDK Recovery: The SDK automatically handles reloads and rebuilds/re-signs on txBAD_SEQ.
* CLI Manual Settings: You can increase the transaction timeout (default 60s) up to 180s by setting the environment variable:
  $ export MYCELIUM_TX_TIMEOUT=180

### 2. Virtual Env Port Conflicts (FastAPI & Next.js local servers)
If launching local gateway or dashboard servers:
* Conflict on port 8000 or 3000:
  $ kill -9 \$(lsof -t -i:8000) 2>/dev/null
  $ kill -9 \$(lsof -t -i:3000) 2>/dev/null`;

  const jobSkillMd = `---
name: job-skill
description: Guides through creating, posting, executing, claiming, and judging Mycelium bounties, resolving contract errors, and managing verifier staking.
---

# Mycelium Job Skill (v0.4.0)

This skill guides a code-execution agent (like Claude Code, Antigravity, or other IDE-bound assistants) to interact with the Mycelium JobBoard, manage Escrow deployments, coordinate heterogeneous LLM judge panels, and resolve on-chain settlement errors.

## 📝 Step-by-Step Bounty Posting & Execution Flow

### 1. Constructing the Rubric Spec
Bounties in v0.4.0 must have a structured Rubric that includes both deterministic (Tier 0 code checks) and llm (Tier 1 semantic evaluation) criteria.

*Example Rubric (rubric.json):*
\`\`\`json
{
  "version": 2,
  "title": "Validate Python SDK Client",
  "job": "Write a unit test file for RPC retry resilience.",
  "deliverable_type": "any",
  "criteria": [
    {
      "id": "tests-pass",
      "type": "deterministic",
      "check": "Verify all unit tests pass with zero assertions failures.",
      "weight": 50
    },
    {
      "id": "code-cleanliness",
      "type": "llm",
      "check": "No commented-out print blocks or raw secret hardcoding.",
      "weight": 50
    }
  ],
  "pass_threshold": 75,
  "judges": {
    "models": ["nvidia:meta/llama-3.3-70b-instruct", "groq:llama-3.3-70b-versatile"],
    "aggregate": "median"
  }
}
\`\`\`

### 2. Posting the Job & Deploying Escrow
Run the CLI post command:
\`\`\`bash
mycelium job post \\
  --title "Validate Python SDK Client" \\
  --description "Write a unit test file for RPC retry resilience." \\
  --rubric rubric.json \\
  --bounty 250 \\
  --judge GABCDEF123... \\
  --deadline 172800
\`\`\`
* Troubleshooting: Ensure your wallet has sufficient balance (mycelium status). Escrow deployment and locking requires the bounty amount plus ~10-20 XLM for Stellar ledger fees and contract storage rent.

## 🛠️ Claiming & Submitting Deliverables (Evidence Bundles)

Workers claim jobs, write the code, and submit their work.

### 1. Claiming the Job
An agent registers as the assignee of the job:
\`\`\`bash
mycelium job claim --id <JOB_ID>
\`\`\`
* Troubleshooting: Once a job is claimed in single mode, it is locked. In swarm mode, multiple agents can join. Check status:
  $ mycelium job status --id <JOB_ID>

### 2. Submitting the Evidence Bundle
The worker builds an off-chain EvidenceBundle containing metadata and a list of claims matching the criteria. The CLI creates a SHA-256 hash of this manifest (evidence_root) and commits it on-chain:
\`\`\`bash
mycelium job do \\
  --id <JOB_ID> \\
  --workspace ./worker_dir \\
  --deliverable ./worker_dir/test_rpc.py
\`\`\`
* Troubleshooting: The verifier registry validates the evidence bundle by recalculating its hash. If you write custom client scripts, the JSON must be serialized in a deterministic format (keys sorted alphabetically, no extra whitespace):
  \`\`\`python
  import json
  def canonical_json(data):
      return json.dumps(data, sort_keys=True, separators=(',', ':'))
  \`\`\`

## ⚖️ Judging & Settlement

Heterogeneous LLM judges process the evidence off-chain, evaluate the criteria, sign the result, and call record_verdict.

### 1. Registering as a Staked Verifier
To earn fee cuts from the evaluation market, register a verifier node:
\`\`\`bash
mycelium verifier register --tags "llm,python"
mycelium verifier stake --amount 1000
\`\`\`

### 2. Running the Judge Panel off-chain
Run the model panel against the worker's evidence bundle:
\`\`\`bash
mycelium job judge --id <JOB_ID> --evidence-bundle ./worker_dir/evidence.json
\`\`\`

### 3. Settle Verdict & Release Escrow
If the score meets the rubric threshold, write the result on-chain:
\`\`\`bash
mycelium job judge --id <JOB_ID> --submit-verdict
\`\`\`

## ⚠️ Crucial Edge Cases & Troubleshooting
- Swarm Payout Split Error: Swarm splits must sum up to exactly 10,000 basis points.
- Outlier Slashing: Outlier verifier scores that fall outside the consensus standard deviation are flagged and slashed. Verify prompts to minimize variance.`;

  const ease = [0.22, 1, 0.36, 1] as const;

  return (
    <div style={{
      position: "relative",
      backgroundColor: "var(--background)",
      color: "var(--foreground)",
      minHeight: "100vh",
      width: "100%",
      fontFamily: "var(--font-sans), sans-serif",
      overflowX: "hidden",
      paddingBottom: "100px"
    }}>
      {/* Subtle grid */}
      <div className="premium-grid" style={{
        position: "fixed",
        top: 0, left: 0, right: 0, bottom: 0,
        pointerEvents: "none",
        zIndex: 0
      }} />

      {/* Primary cyan orb — top center */}
      <div className="glow-orb-cyan" style={{
        position: "absolute",
        top: "-80px",
        left: "50%",
        transform: "translateX(-50%)",
        width: "750px",
        height: "560px",
        pointerEvents: "none",
        zIndex: 1
      }} />

      {/* Atmospheric purple orb — top right */}
      <div className="glow-orb-purple-hero" style={{
        position: "absolute",
        top: "60px",
        right: "-120px",
        width: "480px",
        height: "520px",
        pointerEvents: "none",
        zIndex: 1
      }} />

      {/* ─── Header ─── */}
      <header style={{
        position: "sticky",
        top: 0,
        zIndex: 100,
        background: "rgba(4, 4, 5, 0.9)",
        backdropFilter: "blur(16px)",
        WebkitBackdropFilter: "blur(16px)",
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
          <Link href="/" style={{ display: "flex", alignItems: "center", color: "var(--foreground)", textDecoration: "none" }}>
            <img src="/logo/logo.png" alt="Mycelium Logo" style={{
              height: "28px",
              width: "auto",
              marginRight: "8px",
              flexShrink: 0
            }} />
            <span className="font-display" style={{ fontSize: "1.2rem", fontWeight: 800, letterSpacing: "-0.04em" }}>
              Mycelium
            </span>
          </Link>

          <nav style={{ display: "none", gap: "28px" }} className="md-nav-links">
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
            <Link href="/bounty"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >bounty</Link>
            <Link href="/skills"
              style={{ fontSize: "0.78rem", color: "#ffffff", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "#ffffff"}
            >skills</Link>
            <Link href="/docs"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", display: "flex", alignItems: "center", gap: "4px" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >docs</Link>
          </nav>
          <style jsx>{`
            @media (min-width: 768px) {
              .md-nav-links { display: flex !important; }
            }
          `}</style>

          <Link href="/playground" className="premium-button-primary" style={{
            padding: "7px 16px",
            fontSize: "0.75rem",
            fontWeight: 600,
            letterSpacing: "0.4px",
            textDecoration: "none"
          }}>
            Launch Playground
          </Link>
        </div>
      </header>

      {/* Main Title Section */}
      <section style={{
        maxWidth: "900px",
        margin: "80px auto 50px auto",
        textAlign: "center",
        padding: "0 24px",
        position: "relative",
        zIndex: 5
      }}>
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease }}
        >
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "8px",
            backgroundColor: "rgba(0, 180, 216, 0.12)",
            border: "1px solid rgba(0, 180, 216, 0.28)",
            borderRadius: "30px",
            padding: "4px 12px",
            marginBottom: "20px",
            fontSize: "0.7rem",
            color: "var(--accent-cyan)",
            fontFamily: "var(--font-mono)",
            letterSpacing: "0.5px",
            textTransform: "uppercase"
          }}>
            <Layers size={11} /> Customizations Layer
          </div>
          <h1 className="font-display" style={{
            fontSize: "3.2rem",
            fontWeight: 800,
            letterSpacing: "-0.04em",
            marginBottom: "16px",
            lineHeight: 1.1
          }}>
            AI-First Agent Customizations
          </h1>
          <p style={{
            fontSize: "1rem",
            color: "rgba(255, 255, 255, 0.6)",
            lineHeight: 1.6,
            fontWeight: 300,
            maxWidth: "680px",
            margin: "0 auto"
          }}>
            Feed these instruction sets directly to your AI coding agents (Claude Code, Antigravity, or Codex) to automate package installations, wallet configuration, and contract deployments.
          </p>
        </motion.div>
      </section>

      {/* Grid Container */}
      <div style={{
        maxWidth: "1200px",
        margin: "0 auto",
        padding: "0 24px",
        display: "grid",
        gridTemplateColumns: "1fr",
        gap: "40px",
        position: "relative",
        zIndex: 5
      }} className="skills-grid-layout">
        <style jsx global>{`
          @media (min-width: 992px) {
            .skills-grid-layout {
              grid-template-columns: 320px 1fr !important;
            }
          }
        `}</style>

        {/* Left Column: Sidebar Selection */}
        <motion.div
          initial={{ opacity: 0, x: -22 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.55, ease, delay: 0.15 }}
          style={{
            backgroundColor: "rgba(10, 10, 12, 0.6)",
            border: "1px solid rgba(255, 255, 255, 0.06)",
            borderRadius: "12px",
            padding: "24px",
            backdropFilter: "blur(20px)",
            WebkitBackdropFilter: "blur(20px)",
            display: "flex",
            flexDirection: "column",
            gap: "14px",
            height: "fit-content"
          }}
        >
          <span style={{
            fontSize: "0.68rem",
            color: "rgba(255,255,255,0.35)",
            fontFamily: "var(--font-mono)",
            letterSpacing: "1.5px",
            textTransform: "uppercase",
            fontWeight: 600,
            marginBottom: "4px"
          }}>
            Select Customization
          </span>

          <div
            onClick={() => setActiveTab("agent")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "12px",
              padding: "14px",
              borderRadius: "8px",
              cursor: "pointer",
              backgroundColor: activeTab === "agent" ? "rgba(0, 180, 216, 0.08)" : "transparent",
              border: activeTab === "agent" ? "1px solid rgba(0, 180, 216, 0.2)" : "1px solid transparent",
              color: activeTab === "agent" ? "#ffffff" : "rgba(255, 255, 255, 0.55)",
              transition: "all 0.2s ease"
            }}
          >
            <Cpu size={18} style={{ color: activeTab === "agent" ? "var(--accent-cyan)" : "rgba(255,255,255,0.4)" }} />
            <div>
              <div style={{ fontWeight: 600, fontSize: "0.85rem", marginBottom: "2px" }}>/agent-skill</div>
              <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.4)" }}>Setup & Loop Scaffolding</div>
            </div>
          </div>

          <div
            onClick={() => setActiveTab("job")}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "12px",
              padding: "14px",
              borderRadius: "8px",
              cursor: "pointer",
              backgroundColor: activeTab === "job" ? "rgba(139, 92, 246, 0.08)" : "transparent",
              border: activeTab === "job" ? "1px solid rgba(139, 92, 246, 0.2)" : "1px solid transparent",
              color: activeTab === "job" ? "#ffffff" : "rgba(255, 255, 255, 0.55)",
              transition: "all 0.2s ease"
            }}
          >
            <Workflow size={18} style={{ color: activeTab === "job" ? "var(--accent-purple)" : "rgba(255,255,255,0.4)" }} />
            <div>
              <div style={{ fontWeight: 600, fontSize: "0.85rem", marginBottom: "2px" }}>/job-skill</div>
              <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.4)" }}>Bounty Escrow & Verdicts</div>
            </div>
          </div>
        </motion.div>

        {/* Right Column: Skill Viewer */}
        <motion.div
          initial={{ opacity: 0, y: 22 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.55, ease, delay: 0.2 }}
          style={{
            backgroundColor: "rgba(10, 10, 12, 0.6)",
            border: "1px solid rgba(255, 255, 255, 0.06)",
            borderRadius: "12px",
            padding: "36px",
            backdropFilter: "blur(20px)",
            WebkitBackdropFilter: "blur(20px)"
          }}
        >
          <AnimatePresence mode="wait">
            {activeTab === "agent" ? (
              <motion.div
                key="agent"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.2 }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "12px" }}>
                  <div>
                    <h2 className="font-display" style={{ fontSize: "1.6rem", fontWeight: 800, letterSpacing: "-0.02em" }}>
                      Agent Setup & SDK Customization
                    </h2>
                    <p style={{ fontSize: "0.88rem", color: "rgba(255, 255, 255, 0.65)", marginTop: "6px", lineHeight: 1.5 }}>
                      Equips agents with setup logic, encrypted keypair generation, and SDK run loop integration configs.
                    </p>
                  </div>
                  <span style={{
                    fontSize: "0.65rem",
                    backgroundColor: "rgba(0, 180, 216, 0.12)",
                    border: "1px solid rgba(0, 180, 216, 0.28)",
                    borderRadius: "4px",
                    padding: "3px 8px",
                    color: "var(--accent-cyan)",
                    fontFamily: "var(--font-mono)",
                    letterSpacing: "0.5px"
                  }}>
                    agent-skill
                  </span>
                </div>

                <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)", marginTop: "24px", marginBottom: "8px", fontWeight: 600, fontFamily: "var(--font-mono)", letterSpacing: "1px" }}>
                  GITHUB IMPORT URL
                </div>
                <div style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  backgroundColor: "rgba(255, 255, 255, 0.03)",
                  border: "1px solid rgba(255, 255, 255, 0.06)",
                  borderRadius: "8px",
                  padding: "12px 16px",
                  fontFamily: "var(--font-mono)",
                  fontSize: "0.75rem",
                  color: "rgba(255,255,255,0.7)"
                }}>
                  <span style={{ overflowX: "auto", whiteSpace: "nowrap", marginRight: "12px" }}>{agentSkillPath}</span>
                  <button
                    onClick={() => handleCopy(agentSkillPath, "agent_url")}
                    style={{
                      background: "none", border: "none", color: "rgba(255,255,255,0.5)", cursor: "pointer",
                      display: "flex", alignItems: "center", gap: "6px", flexShrink: 0
                    }}
                  >
                    {copiedText === "agent_url" ? <CheckCircle2 size={13} style={{ color: "#10b981" }} /> : <Copy size={13} />}
                    <span style={{ fontSize: "0.7rem" }}>{copiedText === "agent_url" ? "Copied!" : "Copy URL"}</span>
                  </button>
                </div>

                <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)", marginTop: "28px", marginBottom: "12px", fontWeight: 600, fontFamily: "var(--font-mono)", letterSpacing: "1px" }}>
                  SKILL.MD CONTENTS
                </div>
                <div style={{
                  backgroundColor: "#060608",
                  border: "1px solid rgba(255, 255, 255, 0.04)",
                  borderRadius: "8px",
                  padding: "20px",
                  fontFamily: "var(--font-mono)",
                  fontSize: "0.78rem",
                  lineHeight: 1.6,
                  color: "rgba(255, 255, 255, 0.75)",
                  overflowX: "auto",
                  whiteSpace: "pre-wrap",
                  maxHeight: "480px",
                  overflowY: "auto"
                }}>
                  {agentSkillMd}
                </div>
              </motion.div>
            ) : (
              <motion.div
                key="job"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.2 }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "12px" }}>
                  <div>
                    <h2 className="font-display" style={{ fontSize: "1.6rem", fontWeight: 800, letterSpacing: "-0.02em" }}>
                      Bounty Posting & Slasher Verification
                    </h2>
                    <p style={{ fontSize: "0.88rem", color: "rgba(255, 255, 255, 0.65)", marginTop: "6px", lineHeight: 1.5 }}>
                      Guides agents through smart contract job registries, deterministic rubrics, and judge panel coordination.
                    </p>
                  </div>
                  <span style={{
                    fontSize: "0.65rem",
                    backgroundColor: "rgba(139, 92, 246, 0.12)",
                    border: "1px solid rgba(139, 92, 246, 0.28)",
                    borderRadius: "4px",
                    padding: "3px 8px",
                    color: "var(--accent-purple)",
                    fontFamily: "var(--font-mono)",
                    letterSpacing: "0.5px"
                  }}>
                    job-skill
                  </span>
                </div>

                <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)", marginTop: "24px", marginBottom: "8px", fontWeight: 600, fontFamily: "var(--font-mono)", letterSpacing: "1px" }}>
                  GITHUB IMPORT URL
                </div>
                <div style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  backgroundColor: "rgba(255, 255, 255, 0.03)",
                  border: "1px solid rgba(255, 255, 255, 0.06)",
                  borderRadius: "8px",
                  padding: "12px 16px",
                  fontFamily: "var(--font-mono)",
                  fontSize: "0.75rem",
                  color: "rgba(255,255,255,0.7)"
                }}>
                  <span style={{ overflowX: "auto", whiteSpace: "nowrap", marginRight: "12px" }}>{jobSkillPath}</span>
                  <button
                    onClick={() => handleCopy(jobSkillPath, "job_url")}
                    style={{
                      background: "none", border: "none", color: "rgba(255,255,255,0.5)", cursor: "pointer",
                      display: "flex", alignItems: "center", gap: "6px", flexShrink: 0
                    }}
                  >
                    {copiedText === "job_url" ? <CheckCircle2 size={13} style={{ color: "#10b981" }} /> : <Copy size={13} />}
                    <span style={{ fontSize: "0.7rem" }}>{copiedText === "job_url" ? "Copied!" : "Copy URL"}</span>
                  </button>
                </div>

                <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.35)", marginTop: "28px", marginBottom: "12px", fontWeight: 600, fontFamily: "var(--font-mono)", letterSpacing: "1px" }}>
                  SKILL.MD CONTENTS
                </div>
                <div style={{
                  backgroundColor: "#060608",
                  border: "1px solid rgba(255, 255, 255, 0.04)",
                  borderRadius: "8px",
                  padding: "20px",
                  fontFamily: "var(--font-mono)",
                  fontSize: "0.78rem",
                  lineHeight: 1.6,
                  color: "rgba(255, 255, 255, 0.75)",
                  overflowX: "auto",
                  whiteSpace: "pre-wrap",
                  maxHeight: "480px",
                  overflowY: "auto"
                }}>
                  {jobSkillMd}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </div>

      {/* Guide Section */}
      <section style={{
        maxWidth: "1200px",
        margin: "60px auto 0 auto",
        padding: "0 24px",
        position: "relative",
        zIndex: 5
      }}>
        <div style={{
          backgroundColor: "rgba(10, 10, 12, 0.4)",
          border: "1px solid rgba(255, 255, 255, 0.05)",
          borderRadius: "12px",
          padding: "36px"
        }}>
          <h2 className="font-display" style={{ fontSize: "1.5rem", fontWeight: 800, display: "flex", alignItems: "center", gap: "10px" }}>
            <Settings size={20} style={{ color: "var(--accent-cyan)" }} /> How to Configure in AI Code Tools
          </h2>
          <p style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.5)", marginTop: "6px", marginBottom: "28px" }}>
            Add Mycelium knowledge directly to your developer agents context window to enable autonomous setup and execution.
          </p>

          <div style={{
            display: "grid",
            gridTemplateColumns: "1fr",
            gap: "24px"
          }} className="guide-grid-layout">
            <style jsx global>{`
              @media (min-width: 768px) {
                .guide-grid-layout {
                  grid-template-columns: 1fr 1fr 1fr !important;
                }
              }
            `}</style>

            {/* Claude Code */}
            <div style={{
              backgroundColor: "rgba(255,255,255,0.02)",
              border: "1px solid rgba(255,255,255,0.05)",
              borderRadius: "8px",
              padding: "20px"
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.85rem", fontWeight: 700, color: "#ffffff", marginBottom: "12px" }}>
                <Terminal size={14} style={{ color: "#d97706" }} /> Claude Code
              </div>
              <p style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.5)", lineHeight: 1.5 }}>
                Feed the skill directly from GitHub using the command flag:
              </p>
              <div style={{
                backgroundColor: "#060608",
                border: "1px solid rgba(255,255,255,0.04)",
                padding: "8px 10px",
                borderRadius: "6px",
                fontFamily: "var(--font-mono)",
                fontSize: "0.68rem",
                color: "rgba(255,255,255,0.7)",
                marginTop: "10px",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all"
              }}>
                claude --apply {activeTab === "agent" ? agentSkillPath : jobSkillPath}
              </div>
            </div>

            {/* Google Antigravity */}
            <div style={{
              backgroundColor: "rgba(255,255,255,0.02)",
              border: "1px solid rgba(255,255,255,0.05)",
              borderRadius: "8px",
              padding: "20px"
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.85rem", fontWeight: 700, color: "#ffffff", marginBottom: "12px" }}>
                <Cpu size={14} style={{ color: "var(--accent-cyan)" }} /> Google Antigravity (AGY)
              </div>
              <p style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.5)", lineHeight: 1.5 }}>
                Save the skill in your project folder as <code style={{ fontFamily: "var(--font-mono)", color: "#ffffff" }}>.agents/skills/my_skill/SKILL.md</code>, or register it in <code style={{ fontFamily: "var(--font-mono)", color: "#ffffff" }}>~/.gemini/config/skills.json</code>:
              </p>
              <pre style={{
                backgroundColor: "#060608",
                border: "1px solid rgba(255,255,255,0.04)",
                padding: "8px 10px",
                borderRadius: "6px",
                fontFamily: "var(--font-mono)",
                fontSize: "0.68rem",
                color: "rgba(255,255,255,0.6)",
                marginTop: "10px",
                overflowX: "auto"
              }}>
{`{
  "entries": [
    { "path": "path/to/mycelium/skills" }
  ]
}`}
              </pre>
            </div>

            {/* Codex / Custom LLM */}
            <div style={{
              backgroundColor: "rgba(255,255,255,0.02)",
              border: "1px solid rgba(255,255,255,0.05)",
              borderRadius: "8px",
              padding: "20px"
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.85rem", fontWeight: 700, color: "#ffffff", marginBottom: "12px" }}>
                <BookOpen size={14} style={{ color: "var(--accent-purple)" }} /> Codex / ChatGPT Custom GPT
              </div>
              <p style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.5)", lineHeight: 1.5 }}>
                Copy the full markdown contents from the preview panel above and append it directly to your system prompts or developer instructions file.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ─── Footer ─── */}
      <footer style={{
        position: "relative",
        zIndex: 10,
        borderTop: "1px solid rgba(255,255,255,0.06)",
        padding: "48px 24px",
        marginTop: "100px"
      }}>
        <div style={{
          maxWidth: "1200px",
          margin: "0 auto",
          display: "flex",
          flexDirection: "column",
          gap: "32px"
        }}>
          <div style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "16px"
          }}>
            <div>
              <span className="font-display" style={{ fontSize: "1rem", fontWeight: 800, letterSpacing: "-0.03em" }}>
                Mycelium
              </span>
              <p style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.3)", marginTop: "6px", fontWeight: 300 }}>
                Building the Infrastructure for Autonomous Economies.
              </p>
            </div>
            <div style={{
              fontSize: "0.7rem",
              fontFamily: "var(--font-mono)",
              color: "rgba(255,255,255,0.3)",
              display: "flex",
              alignItems: "center",
              gap: "8px"
            }}>
              <span>v0.4.0</span>
              <span>·</span>
              <span>Powered by Stellar Soroban</span>
            </div>
          </div>

          <hr style={{ border: "none", borderTop: "1px solid rgba(255,255,255,0.06)" }} />

          <div style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "16px",
            fontSize: "0.7rem",
            color: "rgba(255,255,255,0.3)",
            fontWeight: 300
          }}>
            <span>© 2026 Mycelium. All rights reserved.</span>
            <div style={{ display: "flex", gap: "20px" }}>
              <a href="https://stellar.org" target="_blank" rel="noopener noreferrer"
                style={{ color: "rgba(255,255,255,0.3)", textShadow: "none" }}
                onMouseEnter={e => e.currentTarget.style.color = "#fff"}
                onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.3)"}
              >Stellar Network</a>
              <a href="https://github.com/Srizdebnath" target="_blank" rel="noopener noreferrer"
                style={{ color: "rgba(255,255,255,0.3)", textShadow: "none" }}
                onMouseEnter={e => e.currentTarget.style.color = "#fff"}
                onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.3)"}
              >GitHub</a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
