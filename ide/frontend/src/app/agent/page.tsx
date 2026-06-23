"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  Network,
  Cpu,
  Copy,
  Check,
  ExternalLink,
  Shield,
  Zap,
  Play,
  ArrowRight,
  Search,
  Activity,
  Award,
  Globe,
  RefreshCw,
  AlertTriangle,
  Plus
} from "lucide-react";

interface ResolvedAgent {
  id: string;
  name: string;
  role: string;
  x: number;
  y: number;
  color: string;
  status: "Active" | "Standby" | "System Operator";
  address: string;
  capabilityHash: string;
  endpoint: string;
  reputation: number;
  model: string;
  description: string;
}

const REGISTRY_ADDRESS = "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC";
const DEFAULT_NAMES = ["myc_6465185c", "myc2_dd9246f1"];

// Backend API gateway (shared with the playground). Used by the agent-creation wizard.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
// Mirrors mycelium_sdk.scaffold.VALID_FRAMEWORKS / init.py.
const VALID_FRAMEWORKS = ["langgraph", "gemini", "anthropic", "openai", "ollama", "custom"] as const;
// Frameworks whose model list we can discover live (need an API key, except ollama).
const DISCOVERY_FRAMEWORKS = ["gemini", "anthropic", "openai", "ollama"];
const KEYLESS_DISCOVERY = ["ollama"];
const UNIQUE_NAME_RE = /^[a-zA-Z0-9_]{3,30}$/;

export default function AgentNetworkPage() {
  const [agents, setAgents] = useState<ResolvedAgent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<ResolvedAgent | null>(null);
  const [copied, setCopied] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isResolving, setIsResolving] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);

  // Central Registry Manager node details
  const registryNode: ResolvedAgent = {
    id: "hive_registry",
    name: "hive_registry_manager",
    role: "Swarm Directory",
    x: 400,
    y: 250,
    color: "#f43f5e", // Rose
    status: "System Operator",
    address: "Universal Contract ID",
    capabilityHash: "System Registry Core Engine",
    endpoint: "https://soroban-testnet.stellar.org",
    reputation: 999,
    model: "System Level",
    description: "The core directory contract of the Mycelium Hivemind. Deployed on Stellar Testnet, it acts as the canonical entry point mapping names to agent details."
  };

  const decodeBytes = (val: any): string => {
    if (val instanceof Uint8Array) {
      return new TextDecoder().decode(val);
    }
    if (typeof val === "string") {
      return val;
    }
    return String(val);
  };

  const decodeHex = (val: any): string => {
    if (val instanceof Uint8Array) {
      return Array.from(val).map(b => b.toString(16).padStart(2, "0")).join("");
    }
    if (typeof val === "string") {
      return val;
    }
    return String(val);
  };

  // Discover unique agent names by scanning registry events on-chain
  const discoverAgentsOnChain = async (): Promise<string[]> => {
    const StellarSdk = await import("@stellar/stellar-sdk");
    const rpcUrl = "https://soroban-testnet.stellar.org";
    const server = new StellarSdk.rpc.Server(rpcUrl);

    // 1. Fetch latest ledger sequence
    const latestRes = await server.getLatestLedger();
    const latest = latestRes.sequence;

    // 2. Probe to get the oldest retained ledger sequence from RPC node
    const probeRes = await server.getEvents({
      startLedger: Math.max(1, latest - 1),
      filters: [
        {
          type: "contract",
          contractIds: [REGISTRY_ADDRESS]
        }
      ],
      limit: 1
    });
    // Soroban RPC typically retains ~172,800 ledgers (~10 days) of events.
    const oldestLedger = probeRes.oldestLedger || Math.max(1, latest - 172800);

    const discoveredNames = new Set<string>();
    const LEDGER_WINDOW = 16000;
    const MAX_WINDOWS = 64;
    const EVENT_PAGE_LIMIT = 100;

    let lo = oldestLedger;
    let windowCount = 0;

    while (lo <= latest && windowCount < MAX_WINDOWS) {
      const hi = Math.min(lo + LEDGER_WINDOW - 1, latest);
      let cursor: string | undefined = undefined;

      while (true) {
        const queryParams: any = {
          filters: [
            {
              type: "contract",
              contractIds: [REGISTRY_ADDRESS]
            }
          ],
          limit: EVENT_PAGE_LIMIT
        };

        if (cursor === undefined) {
          queryParams.startLedger = lo;
          queryParams.endLedger = hi;
        } else {
          queryParams.cursor = cursor;
        }

        const page = await server.getEvents(queryParams);
        const events = page.events || [];

        for (const event of events) {
          try {
            const topics = event.topic.map(t => StellarSdk.scValToNative(t));
            if (topics.length > 0 && topics[0] === "agent_registered") {
              const val = StellarSdk.scValToNative(event.value);
              let name: string | null = null;
              if (Array.isArray(val)) {
                if (val.length >= 1) name = String(val[0]);
              } else if (typeof val === "object" && val !== null) {
                name = String((val as any).name);
              }
              if (name) {
                discoveredNames.add(name);
              }
            }
          } catch (e) {
            console.warn("Failed to parse event:", e);
          }
          cursor = event.id;
        }

        if (events.length < EVENT_PAGE_LIMIT) {
          break;
        }
      }

      lo = hi + 1;
      windowCount++;
    }

    return Array.from(discoveredNames);
  };

  // Connect to Stellar Soroban Testnet RPC and resolve name
  const resolveAgentOnChain = async (name: string) => {
    const StellarSdk = await import("@stellar/stellar-sdk");
    const rpcUrl = "https://soroban-testnet.stellar.org";
    const server = new StellarSdk.rpc.Server(rpcUrl);

    // Create a dummy signing source account for simulation
    const dummyKeypair = StellarSdk.Keypair.random();
    const source = new StellarSdk.Account(dummyKeypair.publicKey(), "0");

    const argVal = StellarSdk.xdr.ScVal.scvSymbol(name);

    const tx = new StellarSdk.TransactionBuilder(source, {
      fee: "100",
      networkPassphrase: StellarSdk.Networks.TESTNET
    })
      .addOperation(StellarSdk.Operation.invokeContractFunction({
        contract: REGISTRY_ADDRESS,
        function: "resolve_agent",
        args: [argVal]
      }))
      .setTimeout(0)
      .build();

    const simResult = await server.simulateTransaction(tx);
    if (StellarSdk.rpc.Api.isSimulationError(simResult)) {
      throw new Error(simResult.error);
    }

    if (!StellarSdk.rpc.Api.isSimulationSuccess(simResult) || !simResult.result) {
      throw new Error(`Agent '${name}' is not registered in the Hivemind Directory.`);
    }

    const rawVal = simResult.result.retval;
    const nativeMap = StellarSdk.scValToNative(rawVal);

    let address = "";
    let capabilityHash = "";
    let endpoint = "";
    let model = "";
    let role = "";
    let description = "";
    let reputation = 0;

    if (nativeMap instanceof Map) {
      address = nativeMap.get("address") || "";
      const cap = nativeMap.get("capability");
      if (cap) capabilityHash = decodeHex(cap);
      const endp = nativeMap.get("endpoint");
      if (endp) endpoint = decodeBytes(endp);
      const mdl = nativeMap.get("model");
      if (mdl) model = decodeBytes(mdl);
      const rl = nativeMap.get("role");
      if (rl) role = decodeBytes(rl);
      const dsc = nativeMap.get("desc") || nativeMap.get("description");
      if (dsc) description = decodeBytes(dsc);
      const rep = nativeMap.get("reputation");
      if (rep !== undefined) reputation = Number(rep);
    } else if (typeof nativeMap === "object" && nativeMap !== null) {
      const obj = nativeMap as any;
      address = obj.address || "";
      if (obj.capability) capabilityHash = decodeHex(obj.capability);
      if (obj.endpoint) endpoint = decodeBytes(obj.endpoint);
      if (obj.model) model = decodeBytes(obj.model);
      if (obj.role) role = decodeBytes(obj.role);
      const dsc = obj.desc || obj.description;
      if (dsc) description = decodeBytes(dsc);
      reputation = Number(obj.reputation || 0);
    }

    return {
      name,
      address,
      capabilityHash,
      endpoint,
      model,
      role,
      description,
      reputation
    };
  };

  const loadInitialAgents = async () => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      let names = [...DEFAULT_NAMES];
      try {
        const onChainNames = await discoverAgentsOnChain();
        names = Array.from(new Set([...onChainNames, ...DEFAULT_NAMES]));
      } catch (discoveryErr) {
        console.warn("Failed to scan events dynamically, using fallback known names:", discoveryErr);
      }

      const loaded: ResolvedAgent[] = [];
      for (let i = 0; i < names.length; i++) {
        const name = names[i];
        try {
          const chainData = await resolveAgentOnChain(name);
          const color = name === "myc_6465185c" ? "#00f2fe" : name === "myc2_dd9246f1" ? "#8b5cf6" : "#10b981";

          // Position in a circular layout around registry manager (x=400, y=250)
          const angle = (i * 2 * Math.PI) / names.length;
          const radius = 170;
          const x = Math.round(400 + radius * Math.cos(angle));
          const y = Math.round(250 + radius * Math.sin(angle));

          loaded.push({
            id: name,
            name,
            role: chainData.role || "Autonomous Agent",
            x,
            y,
            color,
            status: "Active",
            address: chainData.address,
            capabilityHash: chainData.capabilityHash,
            endpoint: chainData.endpoint,
            reputation: chainData.reputation,
            model: chainData.model || "gemini-2.0-flash",
            description: chainData.description || "Custom on-chain agent resolved from Hive Registry."
          });
        } catch (e) {
          console.warn(`Could not resolve agent ${name}:`, e);
        }
      }

      setAgents(loaded);
      if (loaded.length > 0) {
        setSelectedAgent(loaded[0]);
      } else {
        setSelectedAgent(registryNode);
      }
    } catch (err: any) {
      setErrorMsg("Failed to query Soroban Testnet RPC nodes. Registry contract might be unreachable.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadInitialAgents();
  }, []);

  const handleSearchSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const query = searchQuery.trim();
    if (!query) return;

    // Check if already in list
    const exists = agents.find(a => a.name.toLowerCase() === query.toLowerCase());
    if (exists) {
      setSelectedAgent(exists);
      setSearchQuery("");
      return;
    }

    setIsResolving(true);
    try {
      const chainData = await resolveAgentOnChain(query);
      
      // Calculate position dynamically for the new agent node
      const nodeIndex = agents.length;
      const angle = (nodeIndex * 2 * Math.PI) / (agents.length + 1);
      const radius = 175;
      const x = Math.round(400 + radius * Math.cos(angle));
      const y = Math.round(250 + radius * Math.sin(angle));

      const newAgent: ResolvedAgent = {
        id: query,
        name: query,
        role: chainData.role || "Custom Agent",
        x,
        y,
        color: "#10b981", // Emerald Green for custom resolved nodes
        status: "Active",
        address: chainData.address,
        capabilityHash: chainData.capabilityHash,
        endpoint: chainData.endpoint,
        reputation: chainData.reputation,
        model: chainData.model || "gemini-2.0-flash",
        description: chainData.description || "Dynamic custom agent successfully resolved live from the on-chain Hivemind registry directory contract."
      };

      // Re-position existing agents slightly to balance the circles layout
      const updatedAgents = [...agents, newAgent].map((ag, idx, arr) => {
        if (ag.id === "hive_registry") return ag;
        const offsetAngle = (idx * 2 * Math.PI) / arr.length;
        return {
          ...ag,
          x: Math.round(400 + radius * Math.cos(offsetAngle)),
          y: Math.round(250 + radius * Math.sin(offsetAngle))
        };
      });

      setAgents(updatedAgents);
      setSelectedAgent(newAgent);
      setSearchQuery("");
    } catch (err: any) {
      alert(err.message || "Agent not found in registry.");
    } finally {
      setIsResolving(false);
    }
  };

  const handleCopyAddress = () => {
    navigator.clipboard.writeText(REGISTRY_ADDRESS);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={{
      position: "relative",
      backgroundColor: "var(--background)",
      color: "var(--foreground)",
      minHeight: "100vh",
      width: "100%",
      fontFamily: "var(--font-sans), sans-serif",
      overflowX: "hidden"
    }}>
      {/* Background grid */}
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
              style={{ fontSize: "0.78rem", color: "#ffffff", fontWeight: 500 }}
            >agents</Link>
            <Link href="/bounty"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >bounty</Link>
            <Link href="/docs"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", display: "flex", alignItems: "center", gap: "4px" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
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

      {/* Main Body */}
      <main style={{
        maxWidth: "1200px",
        margin: "0 auto",
        padding: "48px 24px 80px",
        position: "relative",
        zIndex: 10
      }}>
        {/* Title Section */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: "24px", marginBottom: "40px" }}>
          <div>
            <span style={{
              fontSize: "0.68rem",
              fontFamily: "var(--font-mono)",
              color: "var(--accent-cyan)",
              textTransform: "uppercase",
              letterSpacing: "3px",
              fontWeight: "bold",
              display: "block",
              marginBottom: "12px"
            }}>
              ON-CHAIN DIRECTORY ORCHESTRATION
            </span>
            <h1 className="font-display" style={{
              fontSize: "clamp(2rem, 5vw, 3rem)",
              fontWeight: 800,
              color: "#ffffff",
              letterSpacing: "-0.045em",
              marginBottom: "16px"
            }}>
              Swarm Hivemind Registry
            </h1>
            <p style={{
              fontSize: "0.95rem",
              color: "rgba(255, 255, 255, 0.55)",
              maxWidth: "600px",
              fontWeight: 300,
              lineHeight: "1.6"
            }}>
              Monitor active smart-agents directly from Stellar testnet ledger states. Zero mocks.
            </p>
          </div>

          {/* Search bar */}
          <form onSubmit={handleSearchSubmit} style={{ display: "flex", gap: "8px", width: "100%", maxWidth: "340px" }}>
            <input 
              type="text"
              placeholder="Resolve Agent by Name..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              disabled={isResolving}
              style={{
                flex: 1,
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: "6px",
                padding: "8px 14px",
                color: "#ffffff",
                fontSize: "0.85rem",
                outline: "none",
                transition: "border-color 0.2s"
              }}
              onFocus={e => e.currentTarget.style.borderColor = "var(--accent-cyan)"}
              onBlur={e => e.currentTarget.style.borderColor = "rgba(255,255,255,0.08)"}
            />
            <button 
              type="submit"
              disabled={isResolving}
              style={{
                background: "var(--accent-cyan)",
                border: "1px solid var(--accent-cyan)",
                borderRadius: "6px",
                padding: "8px 16px",
                color: "#000000",
                fontSize: "0.85rem",
                fontWeight: 600,
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: "6px",
                transition: "all 0.2s"
              }}
            >
              {isResolving ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={14} />}
              {isResolving ? "Resolving..." : "Add"}
            </button>
          </form>

          {/* Create a brand-new agent (in-IDE scaffolding wizard) */}
          <button
            onClick={() => setShowCreateModal(true)}
            style={{
              background: "linear-gradient(135deg, rgba(139, 92, 246, 0.9), rgba(0, 242, 254, 0.9))",
              border: "none",
              borderRadius: "6px",
              padding: "9px 18px",
              color: "#000000",
              fontSize: "0.85rem",
              fontWeight: 700,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "6px",
              whiteSpace: "nowrap"
            }}
          >
            <Plus size={14} /> Create Agent
          </button>
        </div>

        <AnimatePresence>
          {showCreateModal && (
            <CreateAgentModal onClose={() => setShowCreateModal(false)} />
          )}
        </AnimatePresence>

        {/* Registry Address Card */}
        <div className="premium-card" style={{
          padding: "20px 24px",
          borderRadius: "8px",
          border: "1px solid rgba(0, 242, 254, 0.15)",
          background: "linear-gradient(135deg, rgba(0, 150, 199, 0.03) 0%, rgba(139, 92, 246, 0.03) 100%)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: "16px",
          marginBottom: "32px"
        }}>
          <div>
            <span style={{
              fontSize: "0.65rem",
              fontFamily: "var(--font-mono)",
              color: "rgba(255, 255, 255, 0.4)",
              letterSpacing: "1.5px",
              display: "block",
              marginBottom: "4px"
            }}>
              ON-CHAIN HIVE REGISTRY ADDRESS
            </span>
            <span style={{
              fontFamily: "var(--font-mono)",
              fontSize: "clamp(0.75rem, 2vw, 0.95rem)",
              color: "var(--accent-cyan)",
              fontWeight: 500,
              letterSpacing: "0.5px"
            }}>
              {REGISTRY_ADDRESS}
            </span>
          </div>
          <div style={{ display: "flex", gap: "10px" }}>
            <button 
              onClick={handleCopyAddress}
              style={{
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: "4px",
                padding: "8px 14px",
                color: "#ffffff",
                fontSize: "0.75rem",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: "8px",
                transition: "all 0.2s"
              }}
            >
              {copied ? <Check size={14} style={{ color: "var(--accent-green)" }} /> : <Copy size={14} />}
              {copied ? "Copied!" : "Copy Address"}
            </button>
            <button 
              onClick={loadInitialAgents}
              style={{
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: "4px",
                padding: "8px 14px",
                color: "#ffffff",
                fontSize: "0.75rem",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: "6px"
              }}
            >
              <RefreshCw size={13} />
              Sync Ledger
            </button>
          </div>
        </div>

        {/* Network View & Selection Details Grid */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "1fr",
          gap: "32px",
          alignItems: "start"
        }} className="lg-network-grid">
          <style jsx global>{`
            @media (min-width: 992px) {
              .lg-network-grid {
                grid-template-columns: 1.4fr 1fr !important;
              }
            }
          `}</style>

          {/* SVG Graph View */}
          <div className="premium-card" style={{
            borderRadius: "12px",
            background: "rgba(0,0,0,0.6)",
            border: "1px solid rgba(255, 255, 255, 0.05)",
            overflow: "hidden",
            position: "relative"
          }}>
            {/* Visual Header */}
            <div style={{
              padding: "16px 20px",
              borderBottom: "1px solid rgba(255, 255, 255, 0.06)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between"
            }}>
              <span style={{ fontSize: "0.75rem", fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.5)" }}>
                INTERACTIVE NEURAL GRAPH (LEDGER RESOLVED)
              </span>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span style={{
                  width: "6px", height: "6px",
                  borderRadius: "50%",
                  backgroundColor: isLoading ? "var(--accent-yellow)" : "var(--accent-green)",
                  display: "inline-block"
                }} />
                <span style={{ fontSize: "0.72rem", color: isLoading ? "var(--accent-yellow)" : "var(--accent-green)", fontWeight: 500 }}>
                  {isLoading ? "REFRESHING..." : "LEDGER: CONNECTED"}
                </span>
              </div>
            </div>

            {/* SVG Workspace */}
            <div style={{
              width: "100%",
              height: "460px",
              position: "relative",
              display: "flex",
              alignItems: "center",
              justifyContent: "center"
            }}>
              {isLoading ? (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "16px" }}>
                  <RefreshCw size={36} className="animate-spin" style={{ color: "var(--accent-cyan)" }} />
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem", color: "rgba(255,255,255,0.4)" }}>
                    Querying Soroban Testnet ledgers...
                  </span>
                </div>
              ) : errorMsg ? (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "16px", padding: "24px", textAlign: "center" }}>
                  <AlertTriangle size={36} style={{ color: "var(--accent-yellow)" }} />
                  <span style={{ fontSize: "0.9rem", color: "rgba(255,255,255,0.7)" }}>{errorMsg}</span>
                  <button onClick={loadInitialAgents} className="premium-button-secondary" style={{ padding: "6px 14px", fontSize: "0.75rem" }}>
                    Retry Connection
                  </button>
                </div>
              ) : (
                <svg 
                  viewBox="0 0 800 500" 
                  style={{ width: "100%", height: "100%", display: "block" }}
                >
                  {/* Central Node (Registry) Wires to all agents */}
                  {agents.map((agent, idx) => {
                    const isSelected = selectedAgent?.id === agent.id || selectedAgent?.id === registryNode.id;
                    return (
                      <g key={idx}>
                        <line
                          x1={registryNode.x}
                          y1={registryNode.y}
                          x2={agent.x}
                          y2={agent.y}
                          stroke={isSelected ? "rgba(255, 255, 255, 0.22)" : "rgba(255, 255, 255, 0.05)"}
                          strokeWidth={isSelected ? 1.5 : 1}
                          strokeDasharray={isSelected ? "none" : "4, 4"}
                          style={{ transition: "all 0.3s" }}
                        />
                        {/* Shimmer data lines */}
                        <line
                          x1={registryNode.x}
                          y1={registryNode.y}
                          x2={agent.x}
                          y2={agent.y}
                          stroke={agent.color}
                          strokeWidth={1.5}
                          strokeDasharray="4, 40"
                          strokeDashoffset="10"
                          style={{
                            animation: "shimmer-wire 6s linear infinite"
                          }}
                        />
                      </g>
                    );
                  })}

                  {/* Inter-Agent coordination connection (myc -> myc2) */}
                  {(() => {
                    const ag1 = agents.find(a => a.id === "myc_6465185c");
                    const ag2 = agents.find(a => a.id === "myc2_dd9246f1");
                    if (ag1 && ag2) {
                      const isSelected = selectedAgent?.id === ag1.id || selectedAgent?.id === ag2.id;
                      return (
                        <g>
                          <line
                            x1={ag1.x}
                            y1={ag1.y}
                            x2={ag2.x}
                            y2={ag2.y}
                            stroke={isSelected ? "rgba(0, 242, 254, 0.4)" : "rgba(0, 242, 254, 0.06)"}
                            strokeWidth={1.5}
                            style={{ transition: "all 0.3s" }}
                          />
                        </g>
                      );
                    }
                    return null;
                  })()}

                  <style>{`
                    @keyframes shimmer-wire {
                      to {
                        stroke-dashoffset: -200;
                      }
                    }
                  `}</style>

                  {/* Central Hub Node (Hive Registry) */}
                  <g 
                    onClick={() => setSelectedAgent(registryNode)}
                    style={{ cursor: "pointer" }}
                  >
                    {selectedAgent?.id === registryNode.id && (
                      <circle
                        cx={registryNode.x}
                        cy={registryNode.y}
                        r={34}
                        fill="transparent"
                        stroke={registryNode.color}
                        strokeWidth={1.5}
                        style={{
                          transformOrigin: `${registryNode.x}px ${registryNode.y}px`,
                          animation: "node-glow 2.5s ease-out infinite"
                        }}
                      />
                    )}
                    <circle
                      cx={registryNode.x}
                      cy={registryNode.y}
                      r={28}
                      fill="rgba(4,4,5,0.9)"
                      stroke={selectedAgent?.id === registryNode.id ? registryNode.color : "rgba(255,255,255,0.15)"}
                      strokeWidth={selectedAgent?.id === registryNode.id ? 3 : 1.5}
                      className="node-circle"
                    />
                    <circle
                      cx={registryNode.x}
                      cy={registryNode.y}
                      r={10}
                      fill={registryNode.color}
                    />
                    <text
                      x={registryNode.x}
                      y={registryNode.y + 40}
                      textAnchor="middle"
                      fill={selectedAgent?.id === registryNode.id ? "#ffffff" : "rgba(255,255,255,0.45)"}
                      fontSize="10"
                      fontFamily="var(--font-mono)"
                    >
                      Registry Master
                    </text>
                  </g>

                  {/* Dynamic Agent Nodes */}
                  {agents.map((agent) => {
                    const isSelected = selectedAgent?.id === agent.id;
                    return (
                      <g 
                        key={agent.id}
                        onClick={() => setSelectedAgent(agent)}
                        style={{ cursor: "pointer" }}
                      >
                        {/* Glow for selected */}
                        {isSelected && (
                          <circle
                            cx={agent.x}
                            cy={agent.y}
                            r={30}
                            fill="transparent"
                            stroke={agent.color}
                            strokeWidth={1.5}
                            style={{
                              transformOrigin: `${agent.x}px ${agent.y}px`,
                              animation: "node-glow 2.5s ease-out infinite"
                            }}
                          />
                        )}

                        {/* Interactive Zone */}
                        <circle
                          cx={agent.x}
                          cy={agent.y}
                          r={24}
                          fill="rgba(4,4,5,0.8)"
                          stroke={isSelected ? agent.color : "rgba(255,255,255,0.1)"}
                          strokeWidth={isSelected ? 3 : 1.5}
                          style={{ transition: "all 0.2s" }}
                          className="node-circle"
                        />

                        {/* Center Dot */}
                        <circle
                          cx={agent.x}
                          cy={agent.y}
                          r={8}
                          fill={agent.color}
                        />

                        {/* Label */}
                        <text
                          x={agent.x}
                          y={agent.y + 36}
                          textAnchor="middle"
                          fill={isSelected ? "#ffffff" : "rgba(255,255,255,0.45)"}
                          fontSize="10"
                          fontFamily="var(--font-mono)"
                        >
                          {agent.name}
                        </text>
                      </g>
                    );
                  })}

                  <style>{`
                    @keyframes node-glow {
                      0% {
                        r: 12px;
                        opacity: 0.8;
                      }
                      100% {
                        r: 32px;
                        opacity: 0;
                      }
                    }
                    .node-circle:hover {
                      stroke: #ffffff;
                    }
                    .animate-spin {
                      animation: spin 1.5s linear infinite;
                    }
                    @keyframes spin {
                      to { transform: rotate(360deg); }
                    }
                  `}</style>
                </svg>
              )}
            </div>
          </div>

          {/* Details Panel */}
          <AnimatePresence mode="wait">
            {selectedAgent && (
              <motion.div
                key={selectedAgent.id}
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                className="premium-card"
                style={{
                  borderRadius: "12px",
                  padding: "32px",
                  background: "rgba(10, 10, 12, 0.7)",
                  border: "1px solid rgba(255, 255, 255, 0.06)"
                }}
              >
                {/* Header Info */}
                <div style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: "24px",
                  borderBottom: "1px solid rgba(255,255,255,0.06)",
                  paddingBottom: "18px"
                }}>
                  <div>
                    <span style={{
                      fontSize: "0.68rem",
                      fontFamily: "var(--font-mono)",
                      color: selectedAgent.color,
                      letterSpacing: "1px",
                      fontWeight: "bold",
                      display: "block",
                      textTransform: "uppercase"
                    }}>
                      {selectedAgent.role}
                    </span>
                    <h3 className="font-display" style={{
                      fontSize: "1.45rem",
                      fontWeight: 700,
                      color: "#ffffff",
                      letterSpacing: "-0.02em",
                      marginTop: "4px"
                    }}>
                      {selectedAgent.name}
                    </h3>
                  </div>
                  <div style={{
                    padding: "4px 10px",
                    borderRadius: "4px",
                    border: `1px solid ${selectedAgent.color}40`,
                    background: `${selectedAgent.color}08`,
                    color: selectedAgent.color,
                    fontSize: "0.68rem",
                    fontFamily: "var(--font-mono)",
                    display: "flex",
                    alignItems: "center",
                    gap: "6px"
                  }}>
                    <Activity size={12} />
                    {selectedAgent.status}
                  </div>
                </div>

                {/* Description */}
                <p style={{
                  fontSize: "0.88rem",
                  color: "rgba(255,255,255,0.6)",
                  lineHeight: "1.6",
                  fontWeight: 300,
                  marginBottom: "28px"
                }}>
                  {selectedAgent.description}
                </p>

                {/* Stats / Details */}
                <div style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "18px",
                  marginBottom: "32px"
                }}>
                  {/* Public Wallet */}
                  <div>
                    <span style={{
                      fontSize: "0.62rem",
                      fontFamily: "var(--font-mono)",
                      color: "rgba(255,255,255,0.3)",
                      letterSpacing: "1px",
                      display: "block",
                      marginBottom: "4px",
                      textTransform: "uppercase"
                    }}>
                      Sovereign Wallet address
                    </span>
                    <span style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "0.8rem",
                      color: "rgba(255,255,255,0.85)",
                      wordBreak: "break-all"
                    }}>
                      {selectedAgent.address}
                    </span>
                  </div>

                  {/* Capability Hash */}
                  <div>
                    <span style={{
                      fontSize: "0.62rem",
                      fontFamily: "var(--font-mono)",
                      color: "rgba(255,255,255,0.3)",
                      letterSpacing: "1px",
                      display: "block",
                      marginBottom: "4px",
                      textTransform: "uppercase"
                    }}>
                      Capability Spec Hash (SHA-256)
                    </span>
                    <span style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "0.8rem",
                      color: "rgba(255,255,255,0.85)",
                      wordBreak: "break-all"
                    }}>
                      {selectedAgent.capabilityHash || "None Registered"}
                    </span>
                  </div>

                  {/* Model & Endpoint */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: "16px" }}>
                    {selectedAgent.id !== "hive_registry" && (
                      <div>
                        <span style={{
                          fontSize: "0.62rem",
                          fontFamily: "var(--font-mono)",
                          color: "rgba(255,255,255,0.3)",
                          letterSpacing: "1px",
                          display: "block",
                          marginBottom: "4px",
                          textTransform: "uppercase"
                        }}>
                          Active Endpoint URL
                        </span>
                        <span style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: "0.8rem",
                          color: "rgba(255,255,255,0.85)",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                          wordBreak: "break-all"
                        }}>
                          <Globe size={12} style={{ color: selectedAgent.color }} />
                          {selectedAgent.endpoint ? (
                            <a href={selectedAgent.endpoint} target="_blank" rel="noopener noreferrer" style={{
                              color: "inherit", textDecoration: "underline", textUnderlineOffset: "2px"
                            }}>
                              {selectedAgent.endpoint}
                            </a>
                          ) : (
                            "None Provided"
                          )}
                        </span>
                      </div>
                    )}

                    <div style={{ display: "flex", gap: "24px" }}>
                      <div>
                        <span style={{
                          fontSize: "0.62rem",
                          fontFamily: "var(--font-mono)",
                          color: "rgba(255,255,255,0.3)",
                          letterSpacing: "1px",
                          display: "block",
                          marginBottom: "4px",
                          textTransform: "uppercase"
                        }}>
                          Model Tier
                        </span>
                        <span style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: "0.8rem",
                          color: "rgba(255,255,255,0.85)",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px"
                        }}>
                          <Cpu size={12} style={{ color: selectedAgent.color }} />
                          {selectedAgent.model}
                        </span>
                      </div>

                      <div>
                        <span style={{
                          fontSize: "0.62rem",
                          fontFamily: "var(--font-mono)",
                          color: "rgba(255,255,255,0.3)",
                          letterSpacing: "1px",
                          display: "block",
                          marginBottom: "4px",
                          textTransform: "uppercase"
                        }}>
                          Reputation
                        </span>
                        <span style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: "0.8rem",
                          color: "rgba(255,255,255,0.85)",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px"
                        }}>
                          <Award size={13} style={{ color: "var(--accent-yellow)" }} />
                          {selectedAgent.reputation} Points
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Actions */}
                {selectedAgent.id !== "hive_registry" && (
                  <div style={{ display: "flex", gap: "10px", borderTop: "1px solid rgba(255,255,255,0.06)", paddingTop: "20px" }}>
                    <Link 
                      href="/playground" 
                      className="premium-button-primary"
                      style={{
                        flex: 1,
                        fontSize: "0.82rem",
                        padding: "10px",
                        borderRadius: "6px"
                      }}
                    >
                      <Play size={13} fill="#000" />
                      Interact in IDE
                    </Link>
                    <a 
                      href={`https://horizon-testnet.stellar.org/accounts/${selectedAgent.address}`}
                      target="_blank" 
                      rel="noopener noreferrer" 
                      className="premium-button-secondary"
                      style={{
                        fontSize: "0.82rem",
                        padding: "10px",
                        borderRadius: "6px"
                      }}
                    >
                      View on Horizon
                      <ArrowRight size={13} />
                    </a>
                  </div>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </main>

      {/* Footer */}
      <footer style={{
        position: "relative",
        zIndex: 10,
        borderTop: "1px solid rgba(255,255,255,0.06)",
        padding: "48px 24px",
        marginTop: "80px"
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
            gap: "16px",
            fontSize: "0.7rem",
            color: "rgba(255,255,255,0.3)",
            fontWeight: 300
          }}>
            <span>© 2026 Mycelium. All rights reserved.</span>
            <div style={{ display: "flex", gap: "20px" }}>
              <Link href="/" style={{ color: "rgba(255,255,255,0.3)" }}>Home</Link>
              <Link href="/playground" style={{ color: "rgba(255,255,255,0.3)" }}>IDE Playground</Link>
              <a href="https://github.com/Srizdebnath" target="_blank" rel="noopener noreferrer" style={{ color: "rgba(255,255,255,0.3)" }}>GitHub</a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}

// ── In-IDE Agent Creation wizard ─────────────────────────────────────────────
// Mirrors `mycelium init`: collects project/unique name, provider, API key, and
// model (discovered live via the backend /api/models proxy), then scaffolds a
// new GitHub repo via /api/agents/scaffold and opens the playground in creation
// mode. Requires a GitHub session (shared localStorage JWT with the playground).
function CreateAgentModal({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(1);
  const [projectName, setProjectName] = useState("");
  const [framework, setFramework] = useState<string>("custom");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const jwt = typeof window !== "undefined" ? localStorage.getItem("mycelium_jwt") : null;

  const nameValid = UNIQUE_NAME_RE.test(projectName);
  const canDiscover = DISCOVERY_FRAMEWORKS.includes(framework);
  const needsKey = canDiscover && !KEYLESS_DISCOVERY.includes(framework);

  const discoverModels = async () => {
    setDiscovering(true);
    setDiscoverError(null);
    setModels([]);
    try {
      const res = await fetch(`${API_BASE_URL}/api/models`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${jwt}` },
        body: JSON.stringify({ framework, api_key: apiKey || null }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Model discovery failed");
      setModels(data.models || []);
      if ((data.models || []).length > 0) setModel(data.models[0]);
    } catch (e: any) {
      setDiscoverError(e.message || String(e));
    } finally {
      setDiscovering(false);
    }
  };

  const createAgent = async () => {
    setCreating(true);
    setCreateError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/agents/scaffold`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${jwt}` },
        body: JSON.stringify({
          project_name: projectName,
          framework,
          model: model || "custom",
          unique_name: projectName,
          api_key: apiKey || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Scaffold failed");
      // Open the playground in agent-creation mode for the new repo.
      window.location.href = `/playground?repo=${encodeURIComponent(data.repo)}&mode=create`;
    } catch (e: any) {
      setCreateError(e.message || String(e));
      setCreating(false);
    }
  };

  const overlay: React.CSSProperties = {
    position: "fixed", inset: 0, background: "rgba(4, 4, 5, 0.8)", backdropFilter: "blur(16px)",
    display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: "20px",
  };
  const card: React.CSSProperties = {
    width: "100%", maxWidth: "520px", background: "rgba(10, 10, 12, 0.9)",
    border: "1px solid rgba(255, 255, 255, 0.08)", borderRadius: "12px", padding: "32px",
    boxShadow: "0 20px 50px rgba(0, 0, 0, 0.8)", backdropFilter: "blur(20px)",
  };
  const label: React.CSSProperties = {
    fontSize: "0.7rem", letterSpacing: "1px", color: "rgba(255,255,255,0.45)",
    textTransform: "uppercase", marginBottom: "6px", display: "block", fontFamily: "var(--font-mono)", fontWeight: "bold",
  };
  const input: React.CSSProperties = {
    width: "100%", background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)",
    borderRadius: "6px", padding: "10px 14px", color: "#fff", fontSize: "0.9rem", outline: "none",
    transition: "all 0.2s ease",
  };
  const primaryBtn: React.CSSProperties = {
    background: "linear-gradient(135deg, rgba(139, 92, 246, 0.95), rgba(0, 242, 254, 0.95))",
    border: "none", borderRadius: "6px", padding: "10px 20px", color: "#000", fontWeight: 700,
    cursor: "pointer", fontSize: "0.85rem", transition: "all 0.2s ease",
  };
  const ghostBtn: React.CSSProperties = {
    background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)",
    borderRadius: "6px", padding: "10px 20px", color: "#fff", cursor: "pointer", fontSize: "0.85rem",
  };

  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      style={overlay} onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, y: 10 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, opacity: 0 }}
        style={card} onClick={e => e.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <h2 style={{ fontSize: "1.2rem", fontWeight: 700, color: "#fff", margin: 0 }}>Create a new agent</h2>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "rgba(255,255,255,0.5)", cursor: "pointer", fontSize: "1.2rem" }}>✕</button>
        </div>

        {!jwt ? (
          <div style={{ textAlign: "center", padding: "20px 0" }}>
            <Shield size={32} color="var(--accent-cyan)" style={{ marginBottom: "12px" }} />
            <p style={{ color: "rgba(255,255,255,0.7)", fontSize: "0.9rem", marginBottom: "18px" }}>
              Creating an agent scaffolds a private GitHub repo, so you need to sign in with GitHub first.
            </p>
            <Link href="/playground" style={{ ...primaryBtn, textDecoration: "none", display: "inline-block" }}>
              Sign in via the Playground
            </Link>
          </div>
        ) : (
          <>
            {/* Step indicator */}
            <div style={{ display: "flex", gap: "6px", marginBottom: "22px" }}>
              {[1, 2, 3].map(s => (
                <div key={s} style={{ flex: 1, height: "3px", borderRadius: "2px", background: s <= step ? "var(--accent-cyan)" : "rgba(255,255,255,0.1)" }} />
              ))}
            </div>

            {step === 1 && (
              <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
                <div>
                  <label style={label}>Agent / project name</label>
                  <input style={input} placeholder="my_agent" value={projectName}
                    onChange={e => setProjectName(e.target.value)} />
                  {projectName && !nameValid && (
                    <span style={{ color: "#f87171", fontSize: "0.72rem" }}>Must match ^[a-zA-Z0-9_]{"{3,30}"}$</span>
                  )}
                </div>
                <div>
                  <label style={label}>Provider / framework</label>
                  <select style={input} value={framework} onChange={e => { setFramework(e.target.value); setModels([]); setModel(""); }}>
                    {VALID_FRAMEWORKS.map(f => <option key={f} value={f} style={{ background: "#0b0b14" }}>{f}</option>)}
                  </select>
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                  <button style={ghostBtn} onClick={onClose}>Cancel</button>
                  <button style={{ ...primaryBtn, opacity: nameValid ? 1 : 0.5 }} disabled={!nameValid} onClick={() => setStep(2)}>Next</button>
                </div>
              </div>
            )}

            {step === 2 && (
              <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
                {needsKey && (
                  <div>
                    <label style={label}>{framework} API key</label>
                    <input style={input} type="password" placeholder="sk-… (used only to list models; stored encrypted)"
                      value={apiKey} onChange={e => setApiKey(e.target.value)} />
                  </div>
                )}
                <div>
                  <label style={label}>Model</label>
                  {canDiscover ? (
                    <>
                      <button style={{ ...ghostBtn, marginBottom: "10px" }} disabled={discovering || (needsKey && !apiKey)} onClick={discoverModels}>
                        {discovering ? "Discovering…" : "Discover models"}
                      </button>
                      {models.length > 0 && (
                        <select style={input} value={model} onChange={e => setModel(e.target.value)}>
                          {models.map(m => <option key={m} value={m} style={{ background: "#0b0b14" }}>{m}</option>)}
                        </select>
                      )}
                      {discoverError && <span style={{ color: "#f87171", fontSize: "0.72rem" }}>{discoverError}</span>}
                    </>
                  ) : (
                    <input style={input} placeholder="model id (optional)" value={model} onChange={e => setModel(e.target.value)} />
                  )}
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", gap: "10px" }}>
                  <button style={ghostBtn} onClick={() => setStep(1)}>Back</button>
                  <button style={primaryBtn} onClick={() => setStep(3)}>Next</button>
                </div>
              </div>
            )}

            {step === 3 && (
              <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
                <div style={{ background: "rgba(255,255,255,0.03)", borderRadius: "8px", padding: "16px", fontSize: "0.82rem", color: "rgba(255,255,255,0.8)", lineHeight: 1.7 }}>
                  <div><span style={{ color: "rgba(255,255,255,0.45)" }}>Name:</span> {projectName}</div>
                  <div><span style={{ color: "rgba(255,255,255,0.45)" }}>Provider:</span> {framework}</div>
                  <div><span style={{ color: "rgba(255,255,255,0.45)" }}>Model:</span> {model || "(none)"}</div>
                  <div><span style={{ color: "rgba(255,255,255,0.45)" }}>API key:</span> {apiKey ? "stored encrypted" : "none"}</div>
                </div>
                <p style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.45)", margin: 0 }}>
                  A private GitHub repo will be created with the agent scaffold, then the playground opens in creation mode (Write → Compile → Deploy → Register).
                </p>
                {createError && <span style={{ color: "#f87171", fontSize: "0.78rem" }}>{createError}</span>}
                <div style={{ display: "flex", justifyContent: "space-between", gap: "10px" }}>
                  <button style={ghostBtn} onClick={() => setStep(2)} disabled={creating}>Back</button>
                  <button style={{ ...primaryBtn, opacity: creating ? 0.6 : 1 }} onClick={createAgent} disabled={creating}>
                    {creating ? "Creating…" : "Create agent"}
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </motion.div>
    </motion.div>
  );
}
