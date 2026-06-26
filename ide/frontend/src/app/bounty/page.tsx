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
  Plus,
  Coins,
  Clock,
  Briefcase,
  Users
} from "lucide-react";
import toast, { Toaster } from "react-hot-toast";

interface Job {
  id: number;
  poster: string;
  bounty: number;
  token: string;
  mode: "single" | "swarm";
  escrow: string;
  deadline: number;
  status: "open" | "claimed" | "submitted" | "done" | "cancelled";
  agent: string;
  members: string[];
  shares: number[];
  title: string;
  description: string;
}

const JOB_BOARD_ADDRESS = "CAIGNIJBUA4GKKJBIO27JOAELZQ4KA7AYMB2F5C3W2D3DGQANZZCJGEH";
const HIVE_REGISTRY_ADDRESS = "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC";

export default function BountyBoard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [copied, setCopied] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [showPostModal, setShowPostModal] = useState(false);

  // Wallet connection state matching playground
  const [walletConnected, setWalletConnected] = useState(false);
  const [walletAddress, setWalletAddress] = useState("");
  const [walletNetwork, setWalletNetwork] = useState("");

  const loadJobs = async () => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const StellarSdk = await import("@stellar/stellar-sdk");
      const rpcUrl = "https://soroban-testnet.stellar.org";
      const server = new StellarSdk.rpc.Server(rpcUrl);

      // Simulated dummy account for read-only simulations
      const dummyKeypair = StellarSdk.Keypair.random();
      const source = new StellarSdk.Account(dummyKeypair.publicKey(), "0");

      // 1. Get job count
      let count = 0;
      try {
        const txCount = new StellarSdk.TransactionBuilder(source, {
          fee: "100",
          networkPassphrase: StellarSdk.Networks.TESTNET
        })
          .addOperation(StellarSdk.Operation.invokeContractFunction({
            contract: JOB_BOARD_ADDRESS,
            function: "job_count",
            args: []
          }))
          .setTimeout(0)
          .build();

        const simCount = await server.simulateTransaction(txCount);
        if (StellarSdk.rpc.Api.isSimulationSuccess(simCount) && simCount.result?.retval) {
          count = Number(StellarSdk.scValToNative(simCount.result.retval));
        }
      } catch (err) {
        console.warn("Could not fetch job count on-chain, using fallbacks:", err);
      }

      const fetchedJobs: Job[] = [];
      
      // 2. Fetch details for each job
      for (let i = 1; i <= count; i++) {
        try {
          const txJob = new StellarSdk.TransactionBuilder(source, {
            fee: "100",
            networkPassphrase: StellarSdk.Networks.TESTNET
          })
            .addOperation(StellarSdk.Operation.invokeContractFunction({
              contract: JOB_BOARD_ADDRESS,
              function: "get_job",
              args: [StellarSdk.nativeToScVal(i, { type: "u64" })]
            }))
            .setTimeout(0)
            .build();

          const simJob = await server.simulateTransaction(txJob);
          if (StellarSdk.rpc.Api.isSimulationSuccess(simJob) && simJob.result?.retval) {
            const raw = StellarSdk.scValToNative(simJob.result.retval);
            
            // Resolve job map keys safely
            const poster = raw.get ? raw.get("poster") : raw.poster || "";
            const bountyStroops = raw.get ? raw.get("bounty") : raw.bounty || BigInt(0);
            const token = raw.get ? raw.get("token") : raw.token || "";
            const mode = String(raw.get ? raw.get("mode") : raw.mode || "single") as "single" | "swarm";
            const escrow = raw.get ? raw.get("escrow") : raw.escrow || "";
            const deadline = Number(raw.get ? raw.get("deadline") : raw.deadline || BigInt(0));
            const status = String(raw.get ? raw.get("status") : raw.status || "open") as Job["status"];
            const agent = raw.get ? raw.get("agent") : raw.agent || "";
            const specUriRaw = raw.get ? raw.get("spec_uri") : (raw as any).spec_uri || "";
            
            let specUriStr = "";
            if (specUriRaw instanceof Uint8Array) {
              specUriStr = new TextDecoder().decode(specUriRaw);
            } else if (typeof specUriRaw === "string") {
              specUriStr = specUriRaw;
            }

            // Fetch swarm members and shares if swarm mode
            let members: string[] = [];
            let shares: number[] = [];
            if (mode === "swarm") {
              try {
                const txSwarm = new StellarSdk.TransactionBuilder(source, {
                  fee: "100",
                  networkPassphrase: StellarSdk.Networks.TESTNET
                })
                  .addOperation(StellarSdk.Operation.invokeContractFunction({
                    contract: JOB_BOARD_ADDRESS,
                    function: "get_swarm",
                    args: [StellarSdk.nativeToScVal(i, { type: "u64" })]
                  }))
                  .setTimeout(0)
                  .build();
                const simSwarm = await server.simulateTransaction(txSwarm);
                if (StellarSdk.rpc.Api.isSimulationSuccess(simSwarm) && simSwarm.result?.retval) {
                  const rawSwarm = StellarSdk.scValToNative(simSwarm.result.retval);
                  if (Array.isArray(rawSwarm)) {
                    members = rawSwarm.map(m => typeof m === "string" ? m : m.address || String(m));
                  }
                }
              } catch (e) {
                console.warn("Failed to get swarm for job ID", i, e);
              }

              try {
                const txShares = new StellarSdk.TransactionBuilder(source, {
                  fee: "100",
                  networkPassphrase: StellarSdk.Networks.TESTNET
                })
                  .addOperation(StellarSdk.Operation.invokeContractFunction({
                    contract: JOB_BOARD_ADDRESS,
                    function: "get_shares",
                    args: [StellarSdk.nativeToScVal(i, { type: "u64" })]
                  }))
                  .setTimeout(0)
                  .build();
                const simShares = await server.simulateTransaction(txShares);
                if (StellarSdk.rpc.Api.isSimulationSuccess(simShares) && simShares.result?.retval) {
                  const rawShares = StellarSdk.scValToNative(simShares.result.retval);
                  if (Array.isArray(rawShares)) {
                    shares = rawShares.map(s => Number(s));
                  }
                }
              } catch (e) {
                console.warn("Failed to get shares for job ID", i, e);
              }
            }

            // Parse title & description from specUriStr (if available) or generate a detailed ledger-backed metadata description
            let title = `On-Chain Bounty #${i}`;
            let description = specUriStr || `Sovereign Job #${i} coordination pipeline. Deployed on Stellar Testnet, locking a bounty of ${Number(bountyStroops) / 10000000} XLM. Payout release requires a valid cryptographic proof submitted to escrow contract ${escrow.slice(0, 10)}...${escrow.slice(-6)}.`;
            
            if (specUriStr && specUriStr.includes("|")) {
              const parts = specUriStr.split("|");
              title = parts[0];
              description = parts.slice(1).join("|");
            }

            fetchedJobs.push({
              id: i,
              poster,
              bounty: Number(bountyStroops) / 10000000,
              token,
              mode,
              escrow,
              deadline,
              status,
              agent,
              members,
              shares,
              title,
              description
            });
          }
        } catch (e) {
          console.warn(`Could not resolve job ID ${i}`, e);
        }
      }

      setJobs(fetchedJobs);
      if (fetchedJobs.length > 0) {
        setSelectedJob(fetchedJobs[0]);
      } else {
        setSelectedJob(null);
      }
    } catch (err) {
      console.warn("Failed to read on-chain jobs:", err);
      setJobs([]);
      setSelectedJob(null);
    } finally {
      setIsLoading(false);
    }
  };

  // Connect wallet method using Freighter
  const connectWallet = async () => {
    try {
      const freighter = await import("@stellar/freighter-api");
      const isConnected = await freighter.isConnected();
      if (!isConnected) {
        toast.error("Freighter extension not found. Please install Freighter from freighter.app.");
        return;
      }
      const addressRes = await freighter.requestAccess();
      if (addressRes.address) {
        setWalletAddress(addressRes.address);
        setWalletConnected(true);
        const netRes = await freighter.getNetwork();
        setWalletNetwork(netRes.network || "TESTNET");
        toast.success("Wallet connected!");
      }
    } catch (err: any) {
      toast.error(`Wallet connection failed: ${err.message || err}`);
    }
  };

  useEffect(() => {
    loadJobs();
    // Auto-check Freighter access if already logged in
    const checkExisting = async () => {
      try {
        const freighter = await import("@stellar/freighter-api");
        const isConnected = await freighter.isConnected();
        if (isConnected) {
          const address = await freighter.getAddress();
          if (address && address.address) {
            setWalletAddress(address.address);
            setWalletConnected(true);
            const netRes = await freighter.getNetwork();
            setWalletNetwork(netRes.network || "TESTNET");
          }
        }
      } catch (_) {}
    };
    checkExisting();
  }, []);

  const handleCopyAddress = () => {
    navigator.clipboard.writeText(JOB_BOARD_ADDRESS);
    setCopied(true);
    toast.success("Address copied to clipboard!");
    setTimeout(() => setCopied(false), 2000);
  };

  const activeJobs = jobs.filter(j => j.status !== "done");
  const completedJobs = jobs.filter(j => j.status === "done");

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
      <Toaster position="bottom-right" />
      
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
        left: "15%",
        width: "600px",
        height: "400px",
        pointerEvents: "none",
        zIndex: 1
      }} />
      <div className="glow-orb-purple" style={{
        position: "absolute",
        bottom: "15%",
        right: "15%",
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
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >agents</Link>
            <Link href="/bounty"
              style={{ fontSize: "0.78rem", color: "#ffffff", fontWeight: 500 }}
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

      {/* Main Container */}
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
              SOVEREIGN BOUNTY COORDiNATION
            </span>
            <h1 className="font-display" style={{
              fontSize: "clamp(2rem, 5vw, 3rem)",
              fontWeight: 800,
              color: "#ffffff",
              letterSpacing: "-0.045em",
              marginBottom: "16px"
            }}>
              On-Chain Bounty Board
            </h1>
            <p style={{
              fontSize: "0.95rem",
              color: "rgba(255, 255, 255, 0.55)",
              maxWidth: "600px",
              fontWeight: 300,
              lineHeight: "1.6"
            }}>
              Post jobs requiring single agent or multi-agent swarms. Settlements are escrow-locked on-chain via Soroban.
            </p>
          </div>

          <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
            {!walletConnected ? (
              <button 
                onClick={connectWallet}
                className="premium-button-secondary"
                style={{ padding: "9px 18px", fontSize: "0.85rem", whiteSpace: "nowrap" }}
              >
                Connect Wallet
              </button>
            ) : (
              <div style={{
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.08)",
                borderRadius: "6px",
                padding: "8px 14px",
                fontSize: "0.82rem",
                fontFamily: "var(--font-mono)",
                color: "var(--accent-green)",
                display: "flex",
                alignItems: "center",
                gap: "8px"
              }}>
                <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: "var(--accent-green)" }} />
                {walletAddress.slice(0, 6)}...{walletAddress.slice(-4)} ({walletNetwork})
              </div>
            )}

            <button
              onClick={() => {
                if (!walletConnected) {
                  toast.error("Connect Freighter wallet first to post a job.");
                  return;
                }
                setShowPostModal(true);
              }}
              style={{
                background: "linear-gradient(135deg, rgba(139, 92, 246, 0.95), rgba(0, 242, 254, 0.95))",
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
              <Plus size={14} /> Post Bounty
            </button>
          </div>
        </div>

        {/* Modal Overlay */}
        <AnimatePresence>
          {showPostModal && (
            <CreateBountyModal 
              walletAddress={walletAddress} 
              walletNetwork={walletNetwork} 
              onClose={() => {
                setShowPostModal(false);
                loadJobs();
              }} 
            />
          )}
        </AnimatePresence>

        {/* Board contract Address Card */}
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
              ACTIVE JOB BOARD CONTRACT ADDRESS
            </span>
            <span style={{
              fontFamily: "var(--font-mono)",
              fontSize: "clamp(0.75rem, 2vw, 0.95rem)",
              color: "var(--accent-cyan)",
              fontWeight: 500,
              letterSpacing: "0.5px"
            }}>
              {JOB_BOARD_ADDRESS}
            </span>
          </div>
          <div style={{ display: "flex", gap: "10px" }}>
            <button 
              onClick={handleCopyAddress}
              className="premium-button-secondary"
              style={{
                borderRadius: "4px",
                padding: "8px 14px",
                fontSize: "0.75rem",
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              {copied ? <Check size={14} style={{ color: "var(--accent-green)" }} /> : <Copy size={14} />}
              {copied ? "Copied!" : "Copy Address"}
            </button>
            <button 
              onClick={loadJobs}
              className="premium-button-secondary"
              style={{
                borderRadius: "4px",
                padding: "8px 14px",
                fontSize: "0.75rem",
                display: "flex",
                alignItems: "center",
                gap: "6px"
              }}
            >
              <RefreshCw size={13} className={isLoading ? "animate-spin" : ""} />
              Sync Board
            </button>
          </div>
        </div>

        {/* Empty State */}
        {!isLoading && jobs.length === 0 && (
          <div className="premium-card" style={{
            padding: "48px 32px",
            borderRadius: "12px",
            background: "rgba(10, 10, 12, 0.6)",
            border: "1px solid rgba(255, 255, 255, 0.08)",
            textAlign: "center",
            maxWidth: "600px",
            margin: "0 auto 48px"
          }}>
            <Briefcase size={48} style={{ color: "var(--accent-cyan)", marginBottom: "16px", opacity: 0.8 }} />
            <h3 className="font-display" style={{ fontSize: "1.45rem", fontWeight: 700, color: "#ffffff", marginBottom: "12px", letterSpacing: "-0.02em" }}>
              No Bounties Found On-Chain
            </h3>
            <p style={{ fontSize: "0.9rem", color: "rgba(255, 255, 255, 0.55)", lineHeight: "1.6", marginBottom: "24px", fontWeight: 300 }}>
              There are currently no active smart jobs registered in the Sovereign Job Board contract. Connect your wallet and click "Post Bounty" to deploy an escrow contract and post a new job.
            </p>
            <button
              onClick={() => {
                if (!walletConnected) {
                  toast.error("Connect Freighter wallet first to post a job.");
                  return;
                }
                setShowPostModal(true);
              }}
              style={{
                background: "linear-gradient(135deg, rgba(139, 92, 246, 0.95), rgba(0, 242, 254, 0.95))",
                border: "none",
                borderRadius: "6px",
                padding: "10px 24px",
                color: "#000000",
                fontSize: "0.85rem",
                fontWeight: 700,
                cursor: "pointer",
                transition: "all 0.2s"
              }}
            >
              Post Your First Job
            </button>
          </div>
        )}

        {!isLoading && jobs.length > 0 && (
          <>
            {/* Visual Graph & Job Details Grid */}
        <div style={{
          display: "grid",
          gridTemplateColumns: "1fr",
          gap: "32px",
          alignItems: "start",
          marginBottom: "48px"
        }} className="lg-network-grid">
          <style jsx global>{`
            @media (min-width: 992px) {
              .lg-network-grid {
                grid-template-columns: 1.4fr 1fr !important;
              }
            }
          `}</style>

          {/* Interactive Swarm Visualizer */}
          <div className="premium-card" style={{
            borderRadius: "12px",
            background: "rgba(0,0,0,0.6)",
            border: "1px solid rgba(255, 255, 255, 0.05)",
            overflow: "hidden",
            position: "relative"
          }}>
            {/* Visual Graph Header */}
            <div style={{
              padding: "16px 20px",
              borderBottom: "1px solid rgba(255, 255, 255, 0.06)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between"
            }}>
              <span style={{ fontSize: "0.75rem", fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.5)" }}>
                INTERACTIVE SWARM VISUALIZATION
              </span>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <span style={{
                  width: "6px", height: "6px",
                  borderRadius: "50%",
                  backgroundColor: isLoading ? "var(--accent-yellow)" : "var(--accent-green)",
                  display: "inline-block"
                }} />
                <span style={{ fontSize: "0.72rem", color: isLoading ? "var(--accent-yellow)" : "var(--accent-green)", fontWeight: 500 }}>
                  {isLoading ? "LOADING..." : "BOARD SYNCED"}
                </span>
              </div>
            </div>

            {/* SVG Swarm Canvas */}
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
                    Querying Soroban Testnet RPC...
                  </span>
                </div>
              ) : selectedJob ? (
                <svg 
                  viewBox="0 0 800 500" 
                  style={{ width: "100%", height: "100%", display: "block" }}
                >
                  <style>{`
                    @keyframes shimmer-wire {
                      to {
                        stroke-dashoffset: -200;
                      }
                    }
                    @keyframes pulse-node {
                      0%, 100% { r: 24px; opacity: 0.15; }
                      50% { r: 36px; opacity: 0.4; }
                    }
                    .node-c:hover {
                      stroke: #ffffff !important;
                    }
                  `}</style>

                  {/* Central Job Node */}
                  {(() => {
                    const cx = 400;
                    const cy = 250;
                    const isSwarm = selectedJob.mode === "swarm";
                    
                    // Connected nodes coordinates
                    const outerNodes: { id: string; label: string; x: number; y: number; color: string; desc: string }[] = [];
                    
                    if (selectedJob.mode === "single" && selectedJob.agent) {
                      // Single agent node
                      outerNodes.push({
                        id: selectedJob.agent,
                        label: selectedJob.agent === "myc_6465185c" ? "myc_6465185c" : selectedJob.agent.slice(0, 10) + "...",
                        x: 400,
                        y: 80,
                        color: "#00f2fe",
                        desc: "Sovereign Claiming Agent"
                      });
                    } else if (selectedJob.mode === "swarm" && selectedJob.members && selectedJob.members.length > 0) {
                      // Multi agent nodes in a circle
                      selectedJob.members.forEach((m, idx) => {
                        const angle = (idx * 2 * Math.PI) / selectedJob.members.length - Math.PI / 2;
                        const radius = 150;
                        outerNodes.push({
                          id: m,
                          label: m.length > 15 ? m.slice(0, 10) + "..." : m,
                          x: Math.round(cx + radius * Math.cos(angle)),
                          y: Math.round(cy + radius * Math.sin(angle)),
                          color: idx === 0 ? "#8b5cf6" : idx === 1 ? "#00f2fe" : "#10b981",
                          desc: `Swarm Member #${idx + 1}`
                        });
                      });
                    }

                    return (
                      <g>
                        {/* Wires connecting center Job to agents */}
                        {outerNodes.map((n, i) => (
                          <g key={n.id}>
                            <line 
                              x1={cx} y1={cy}
                              x2={n.x} y2={n.y}
                              stroke="rgba(255, 255, 255, 0.15)"
                              strokeWidth={2}
                              strokeDasharray="4,4"
                            />
                            {/* Animated data shimmer line */}
                            <line 
                              x1={cx} y1={cy}
                              x2={n.x} y2={n.y}
                              stroke={n.color}
                              strokeWidth={2}
                              strokeDasharray="8, 30"
                              style={{
                                animation: "shimmer-wire 6s linear infinite"
                              }}
                            />
                          </g>
                        ))}

                        {/* Unclaimed dashed Ring representation */}
                        {outerNodes.length === 0 && (
                          <g>
                            <circle 
                              cx={cx} cy={cy} r={120}
                              fill="transparent"
                              stroke="rgba(255, 255, 255, 0.05)"
                              strokeWidth={1.5}
                              strokeDasharray="6, 6"
                            />
                            <text
                              x={cx} y={cy - 140}
                              textAnchor="middle"
                              fill="rgba(255,255,255,0.25)"
                              fontSize="11"
                              fontFamily="var(--font-mono)"
                              letterSpacing="1"
                            >
                              AWAITING ACTIVE CLAIMS...
                            </text>
                          </g>
                        )}

                        {/* Center Job Node styling */}
                        <circle 
                          cx={cx} cy={cy} r={32}
                          fill="transparent"
                          stroke={isSwarm ? "#8b5cf6" : "#00f2fe"}
                          strokeWidth={1}
                          style={{
                            transformOrigin: `${cx}px ${cy}px`,
                            animation: "pulse-node 3s ease-out infinite"
                          }}
                        />
                        <circle 
                          cx={cx} cy={cy} r={24}
                          fill="rgba(10, 10, 12, 0.9)"
                          stroke={isSwarm ? "#8b5cf6" : "#00f2fe"}
                          strokeWidth={2.5}
                          className="node-c"
                        />
                        <Briefcase cx={cx} cy={cy} size={20} x={cx - 10} y={cy - 10} style={{ color: isSwarm ? "#8b5cf6" : "#00f2fe" }} />
                        <text
                          x={cx} y={cy + 42}
                          textAnchor="middle"
                          fill="#ffffff"
                          fontSize="11"
                          fontFamily="var(--font-mono)"
                          fontWeight="bold"
                        >
                          JOB #{selectedJob.id}
                        </text>
                        <text
                          x={cx} y={cy + 56}
                          textAnchor="middle"
                          fill="rgba(255,255,255,0.4)"
                          fontSize="9"
                          fontFamily="var(--font-mono)"
                        >
                          {selectedJob.mode.toUpperCase()} MODE
                        </text>

                        {/* Outer Agent Nodes */}
                        {outerNodes.map((n) => (
                          <g key={n.id} style={{ cursor: "pointer" }}>
                            <circle 
                              cx={n.x} cy={n.y} r={20}
                              fill="rgba(10, 10, 12, 0.9)"
                              stroke={n.color}
                              strokeWidth={2}
                              className="node-c"
                            />
                            <Cpu size={14} x={n.x - 7} y={n.y - 7} style={{ color: n.color }} />
                            <text
                              x={n.x} y={n.y + 32}
                              textAnchor="middle"
                              fill="#ffffff"
                              fontSize="11"
                              fontFamily="var(--font-mono)"
                            >
                              {n.label}
                            </text>
                            <text
                              x={n.x} y={n.y + 44}
                              textAnchor="middle"
                              fill="rgba(255,255,255,0.4)"
                              fontSize="9"
                              fontFamily="var(--font-mono)"
                            >
                              {n.desc}
                            </text>
                          </g>
                        ))}
                      </g>
                    );
                  })()}
                </svg>
              ) : (
                <div style={{ color: "rgba(255,255,255,0.4)", fontSize: "0.85rem" }}>
                  Select a bounty to view coordination graph.
                </div>
              )}
            </div>
          </div>

          {/* Details Panel */}
          <AnimatePresence mode="wait">
            {selectedJob && (
              <motion.div
                key={selectedJob.id}
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
                      color: selectedJob.mode === "swarm" ? "var(--accent-purple)" : "var(--accent-cyan)",
                      letterSpacing: "1px",
                      fontWeight: "bold",
                      display: "block",
                      textTransform: "uppercase"
                    }}>
                      {selectedJob.mode === "swarm" ? "Swarm Payout Coordination" : "Single Agent Payout"}
                    </span>
                    <h3 className="font-display" style={{
                      fontSize: "1.45rem",
                      fontWeight: 700,
                      color: "#ffffff",
                      letterSpacing: "-0.02em",
                      marginTop: "4px"
                    }}>
                      {selectedJob.title}
                    </h3>
                  </div>
                  <div style={{
                    padding: "4px 10px",
                    borderRadius: "4px",
                    border: `1px solid ${
                      selectedJob.status === "done" 
                        ? "var(--accent-green)" 
                        : selectedJob.status === "open"
                          ? "var(--accent-cyan)"
                          : "var(--accent-yellow)"
                    }40`,
                    background: `${
                      selectedJob.status === "done" 
                        ? "var(--accent-green)" 
                        : selectedJob.status === "open"
                          ? "var(--accent-cyan)"
                          : "var(--accent-yellow)"
                    }08`,
                    color: selectedJob.status === "done" 
                      ? "var(--accent-green)" 
                      : selectedJob.status === "open"
                        ? "var(--accent-cyan)"
                        : "var(--accent-yellow)",
                    fontSize: "0.68rem",
                    fontFamily: "var(--font-mono)",
                    textTransform: "uppercase",
                    display: "flex",
                    alignItems: "center",
                    gap: "6px"
                  }}>
                    <Activity size={12} />
                    {selectedJob.status}
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
                  {selectedJob.description}
                </p>

                {/* Details list */}
                <div style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "18px",
                  marginBottom: "32px"
                }}>
                  {/* Bounty Lock */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
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
                        LOCKED BOUNTY AMOUNT
                      </span>
                      <span style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "1.1rem",
                        color: "#ffffff",
                        fontWeight: 700,
                        display: "flex",
                        alignItems: "center",
                        gap: "6px"
                      }}>
                        <Coins size={15} style={{ color: "var(--accent-yellow)" }} />
                        {selectedJob.bounty.toFixed(4)} XLM
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
                        REFUND DEADLINE
                      </span>
                      <span style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "0.85rem",
                        color: "rgba(255,255,255,0.85)",
                        display: "flex",
                        alignItems: "center",
                        gap: "6px"
                      }}>
                        <Clock size={14} style={{ color: "rgba(255,255,255,0.5)" }} />
                        {selectedJob.deadline ? new Date(selectedJob.deadline * 1000).toLocaleString() : "Never"}
                      </span>
                    </div>
                  </div>

                  {/* Poster Address */}
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
                      POSTER WALLET ADDRESS
                    </span>
                    <span style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "0.8rem",
                      color: "rgba(255,255,255,0.85)",
                      wordBreak: "break-all"
                    }}>
                      {selectedJob.poster}
                    </span>
                  </div>

                  {/* Escrow Address */}
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
                      CONDITIONAL ESCROW CONTRACT
                    </span>
                    <span style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "0.8rem",
                      color: "rgba(255,255,255,0.85)",
                      wordBreak: "break-all"
                    }}>
                      {selectedJob.escrow || "None Deployed"}
                    </span>
                  </div>

                  {/* Swarm / Agent details */}
                  <div style={{ borderTop: "1px solid rgba(255,255,255,0.06)", paddingTop: "18px" }}>
                    {selectedJob.mode === "swarm" ? (
                      <div>
                        <span style={{
                          fontSize: "0.62rem",
                          fontFamily: "var(--font-mono)",
                          color: "rgba(255,255,255,0.3)",
                          letterSpacing: "1px",
                          display: "block",
                          marginBottom: "10px",
                          textTransform: "uppercase"
                        }}>
                          ACTIVE SWARM CLAIMS ({selectedJob.members.length})
                        </span>
                        {selectedJob.members.length > 0 ? (
                          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                            {selectedJob.members.map((m, idx) => (
                              <div 
                                key={m}
                                style={{
                                  background: "rgba(255,255,255,0.02)",
                                  border: "1px solid rgba(255,255,255,0.05)",
                                  borderRadius: "6px",
                                  padding: "8px 12px",
                                  display: "flex",
                                  justifyContent: "space-between",
                                  alignItems: "center"
                                }}
                              >
                                <span 
                                  title={m}
                                  style={{ 
                                    fontFamily: "var(--font-mono)", 
                                    fontSize: "0.78rem", 
                                    color: "#ffffff",
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                    marginRight: "8px"
                                  }}
                                >
                                  {m.slice(0, 10)}...{m.slice(-6)}
                                </span>
                                <span style={{
                                  fontFamily: "var(--font-mono)",
                                  fontSize: "0.7rem",
                                  background: "rgba(139, 92, 246, 0.15)",
                                  border: "1px solid rgba(139, 92, 246, 0.3)",
                                  padding: "2px 8px",
                                  borderRadius: "4px",
                                  color: "var(--accent-purple)",
                                  flexShrink: 0
                                }}>
                                  {selectedJob.shares && selectedJob.shares[idx] !== undefined
                                    ? `${selectedJob.shares[idx]} bps (${selectedJob.shares[idx] / 100}%)`
                                    : idx === 0 ? "5000 bps (50%)" : idx === 1 ? "3000 bps (30%)" : "2000 bps (20%)"}
                                </span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div style={{ fontSize: "0.8rem", color: "rgba(255,255,255,0.4)" }}>
                            No agent swarm has joined this bounty yet.
                          </div>
                        )}
                      </div>
                    ) : (
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
                          CLAIMANT AGENT ADDRESS
                        </span>
                        <span 
                          title={selectedJob.agent || "Unclaimed"}
                          style={{
                            fontFamily: "var(--font-mono)",
                            fontSize: "0.8rem",
                            color: selectedJob.agent ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.4)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            display: "block"
                          }}
                        >
                          {selectedJob.agent ? `${selectedJob.agent.slice(0, 12)}...${selectedJob.agent.slice(-8)}` : "Unclaimed"}
                        </span>
                      </div>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div style={{ display: "flex", gap: "10px", borderTop: "1px solid rgba(255,255,255,0.06)", paddingTop: "20px" }}>
                  <a 
                    href={`https://horizon-testnet.stellar.org/accounts/${selectedJob.escrow}`}
                    target="_blank" 
                    rel="noopener noreferrer" 
                    className="premium-button-primary"
                    style={{
                      flex: 1,
                      fontSize: "0.82rem",
                      padding: "10px",
                      borderRadius: "6px"
                    }}
                  >
                    <Globe size={13} fill="#000" />
                    Inspect Escrow Ledger
                  </a>
                  <a 
                    href={`https://horizon-testnet.stellar.org/accounts/${JOB_BOARD_ADDRESS}`}
                    target="_blank" 
                    rel="noopener noreferrer" 
                    className="premium-button-secondary"
                    style={{
                      fontSize: "0.82rem",
                      padding: "10px",
                      borderRadius: "6px"
                    }}
                  >
                    View Job Board
                    <ArrowRight size={13} />
                  </a>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Lists sections */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: "32px", marginTop: "32px" }}>
          {/* Active Job Cards */}
          <div>
            <h2 className="font-display" style={{ fontSize: "1.4rem", color: "#ffffff", fontWeight: 700, marginBottom: "18px", display: "flex", alignItems: "center", gap: "8px" }}>
              <Briefcase size={20} style={{ color: "var(--accent-cyan)" }} />
              Active Bounties ({activeJobs.length})
            </h2>
            <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: "16px" }} className="md-cards-grid">
              <style jsx global>{`
                @media (min-width: 768px) {
                  .md-cards-grid {
                    grid-template-columns: repeat(2, 1fr) !important;
                  }
                }
              `}</style>
              {activeJobs.map(job => (
                <div 
                  key={job.id}
                  onClick={() => setSelectedJob(job)}
                  className="premium-card"
                  style={{
                    borderRadius: "8px",
                    padding: "20px 24px",
                    border: selectedJob?.id === job.id ? "1px solid var(--accent-cyan)" : "1px solid rgba(255, 255, 255, 0.08)",
                    cursor: "pointer",
                    background: selectedJob?.id === job.id ? "rgba(0, 150, 199, 0.03)" : "rgba(255, 255, 255, 0.01)"
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "12px" }}>
                    <span style={{
                      fontSize: "0.65rem",
                      fontFamily: "var(--font-mono)",
                      color: job.mode === "swarm" ? "var(--accent-purple)" : "var(--accent-cyan)",
                      textTransform: "uppercase",
                      letterSpacing: "1px"
                    }}>{job.mode} bounty</span>
                    <span style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "0.85rem",
                      fontWeight: 700,
                      color: "var(--accent-yellow)"
                    }}>{job.bounty} XLM</span>
                  </div>
                  <h3 className="font-display" style={{ fontSize: "1.1rem", fontWeight: 700, color: "#ffffff", marginBottom: "8px" }}>{job.title}</h3>
                  <p style={{
                    fontSize: "0.82rem",
                    color: "rgba(255, 255, 255, 0.5)",
                    lineHeight: "1.5",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical"
                  }}>{job.description}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Completed Job Cards */}
          <div>
            <h2 className="font-display" style={{ fontSize: "1.4rem", color: "#ffffff", fontWeight: 700, marginBottom: "18px", display: "flex", alignItems: "center", gap: "8px" }}>
              <Award size={20} style={{ color: "var(--accent-green)" }} />
              Completed Bounties ({completedJobs.length})
            </h2>
            <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: "16px" }} className="md-cards-grid">
              {completedJobs.map(job => (
                <div 
                  key={job.id}
                  onClick={() => setSelectedJob(job)}
                  className="premium-card"
                  style={{
                    borderRadius: "8px",
                    padding: "20px 24px",
                    border: selectedJob?.id === job.id ? "1px solid var(--accent-green)" : "1px solid rgba(255, 255, 255, 0.05)",
                    cursor: "pointer",
                    background: selectedJob?.id === job.id ? "rgba(15, 159, 120, 0.03)" : "rgba(255, 255, 255, 0.005)"
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "12px" }}>
                    <span style={{
                      fontSize: "0.65rem",
                      fontFamily: "var(--font-mono)",
                      color: "var(--accent-green)",
                      textTransform: "uppercase",
                      letterSpacing: "1px"
                    }}>Finalized</span>
                    <span style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: "0.85rem",
                      fontWeight: 700,
                      color: "rgba(255,255,255,0.4)"
                    }}>{job.bounty} XLM</span>
                  </div>
                  <h3 className="font-display" style={{ fontSize: "1.1rem", fontWeight: 700, color: "rgba(255,255,255,0.6)", marginBottom: "8px" }}>{job.title}</h3>
                  <p style={{
                    fontSize: "0.82rem",
                    color: "rgba(255, 255, 255, 0.35)",
                    lineHeight: "1.5",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical"
                  }}>{job.description}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
          </>
        )}

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

// ── In-IDE Bounty Posting wizard ─────────────────────────────────────────────
// Mirrors the SDK's EscrowPaymentRouter.create_locked_escrow + post_job on the JobBoard contract.
// Signs and broadcasts everything directly through Freighter wallet.
interface CreateBountyModalProps {
  walletAddress: string;
  walletNetwork: string;
  onClose: () => void;
}

function CreateBountyModal({ walletAddress, walletNetwork, onClose }: CreateBountyModalProps) {
  const [step, setStep] = useState(1);
  const [jobTitle, setJobTitle] = useState("");
  const [jobDescription, setJobDescription] = useState("");
  const [mode, setMode] = useState<"single" | "swarm">("single");

  const [bountyXlm, setBountyXlm] = useState("10");
  const [deadlineSeconds, setDeadlineSeconds] = useState("86400"); // 24 hours
  const [tokenAddress, setTokenAddress] = useState("CAS3KPT76XQ4NCJ7GD43W57KQC7N6F72M7U72M7U72M7U72M7U72MSAC"); // Testnet native XLM token address fallback

  // Execution states
  const [postingProgress, setPostingProgress] = useState<{
    step: "wasm" | "deploy" | "lock" | "register" | "completed" | "error";
    errorMsg: string | null;
    wasmHash: string | null;
    escrowId: string | null;
    jobId: number | null;
  }>({
    step: "wasm",
    errorMsg: null,
    wasmHash: null,
    escrowId: null,
    jobId: null
  });

  const isFormValid = jobTitle.trim().length >= 4 && jobDescription.trim().length >= 10;
  const isBountyValid = parseFloat(bountyXlm) > 0 && parseInt(deadlineSeconds) > 0;

  const runOnChainPostFlow = async () => {
    setStep(3);
    setPostingProgress({
      step: "wasm",
      errorMsg: null,
      wasmHash: null,
      escrowId: null,
      jobId: null
    });

    try {
      const StellarSdk = await import("@stellar/stellar-sdk");
      const freighter = await import("@stellar/freighter-api");

      const isTestnet = walletNetwork !== "PUBLIC";
      const rpcUrl = isTestnet ? "https://soroban-testnet.stellar.org" : "https://mainnet.sorobanrpc.com";
      const horizonUrl = isTestnet ? "https://horizon-testnet.stellar.org" : "https://horizon.stellar.org";
      const networkPassphrase = isTestnet ? StellarSdk.Networks.TESTNET : StellarSdk.Networks.PUBLIC;
      const server = new StellarSdk.rpc.Server(rpcUrl);

      // Helper to poll transactions
      const pollTx = async (txHash: string) => {
        let status = await server.getTransaction(txHash);
        let retries = 15;
        while (status.status === "NOT_FOUND" && retries > 0) {
          await new Promise(r => setTimeout(r, 2000));
          status = await server.getTransaction(txHash);
          retries--;
        }
        if (status.status !== "SUCCESS") {
          throw new Error(`Transaction ${txHash} did not succeed. Status: ${status.status}`);
        }
        return status;
      };

      // 1. Fetch escrow.wasm binary from the local server
      const wasmRes = await fetch("/escrow.wasm");
      if (!wasmRes.ok) throw new Error("Could not download escrow.wasm deployment target from public assets.");
      const wasmBytes = new Uint8Array(await wasmRes.arrayBuffer());

      // Fetch sequence number
      const accRes = await fetch(`${horizonUrl}/accounts/${walletAddress}`);
      if (!accRes.ok) throw new Error(`Could not query account details: ${accRes.statusText}`);
      const accData = await accRes.json();
      let sourceAccount = new StellarSdk.Account(walletAddress, accData.sequence);

      // Step 3.1: Upload Escrow WASM
      let txUpload = new StellarSdk.TransactionBuilder(sourceAccount, { fee: "1000000", networkPassphrase })
        .addOperation(StellarSdk.Operation.uploadContractWasm({ wasm: wasmBytes }))
        .setTimeout(0)
        .build();

      let simUpload = await server.simulateTransaction(txUpload);
      if ((simUpload as any).error) throw new Error(`Upload simulation failed: ${(simUpload as any).error}`);
      txUpload = StellarSdk.rpc.assembleTransaction(txUpload, simUpload).build();
      const uploadHash = txUpload.hash().toString("hex");

      const signUpload = await freighter.signTransaction(txUpload.toXDR(), { networkPassphrase });
      const signedUpload = StellarSdk.TransactionBuilder.fromXDR(signUpload.signedTxXdr, networkPassphrase);
      
      const submitUpload = await server.sendTransaction(signedUpload);
      if (submitUpload.status === "ERROR") throw new Error(`Upload rejected: ${JSON.stringify((submitUpload as any).errorResult)}`);
      
      const uploadCommit = await pollTx(uploadHash);
      
      // Parse WASM ID hash
      let wasmHash = "";
      const retVal = (uploadCommit as any).returnValue;
      if (retVal && retVal.switch().name === "scvBytes") {
        wasmHash = Array.from(new Uint8Array(retVal.bytes())).map(b => b.toString(16).padStart(2, '0')).join('');
      } else {
        // Fallback option parsing
        const resultMeta = (uploadCommit as any).resultMetaXdr;
        if (resultMeta) {
          const meta = StellarSdk.xdr.TransactionMeta.fromXDR(resultMeta, "base64");
          const returnVal = (meta as any).v3?.().sorobanMeta?.()?.returnValue?.();
          if (returnVal) {
            wasmHash = Array.from(new Uint8Array(returnVal.bytes())).map(b => b.toString(16).padStart(2, '0')).join('');
          }
        }
      }

      if (!wasmHash) throw new Error("Could not parse WASM Code ID from commit transaction meta.");
      
      setPostingProgress(prev => ({ ...prev, step: "deploy", wasmHash }));

      // Step 3.2: Instantiate Escrow Contract
      const accRes2 = await fetch(`${horizonUrl}/accounts/${walletAddress}`);
      const accData2 = await accRes2.json();
      sourceAccount = new StellarSdk.Account(walletAddress, accData2.sequence);

      const hexToBytes = (hex: string) => {
        const bytes = new Uint8Array(hex.length / 2);
        for (let i = 0; i < hex.length; i += 2) {
          bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
        }
        return bytes;
      };

      let txCreate = new StellarSdk.TransactionBuilder(sourceAccount, { fee: "1000000", networkPassphrase })
        .addOperation(StellarSdk.Operation.createCustomContract({
          address: new StellarSdk.Address(walletAddress),
          wasmHash: hexToBytes(wasmHash)
        }))
        .setTimeout(0)
        .build();

      const simCreate = await server.simulateTransaction(txCreate);
      if ((simCreate as any).error) throw new Error(`Instantiation simulation failed: ${(simCreate as any).error}`);
      txCreate = StellarSdk.rpc.assembleTransaction(txCreate, simCreate).build();
      const createHash = txCreate.hash().toString("hex");

      const signCreate = await freighter.signTransaction(txCreate.toXDR(), { networkPassphrase });
      const signedCreate = StellarSdk.TransactionBuilder.fromXDR(signCreate.signedTxXdr, networkPassphrase);

      const submitCreate = await server.sendTransaction(signedCreate);
      if (submitCreate.status === "ERROR") throw new Error(`Instantiation rejected: ${JSON.stringify((submitCreate as any).errorResult)}`);

      const createCommit = await pollTx(createHash);

      // Parse Escrow Contract Address
      let escrowId = "";
      const retValCreate = (createCommit as any).returnValue;
      if (retValCreate && retValCreate.switch().name === "scvAddress") {
        escrowId = StellarSdk.Address.fromScVal(retValCreate).toString();
      } else {
        const resultMeta = (createCommit as any).resultMetaXdr;
        if (resultMeta) {
          const meta = StellarSdk.xdr.TransactionMeta.fromXDR(resultMeta, "base64");
          const returnVal = (meta as any).v3?.().sorobanMeta?.()?.returnValue?.();
          if (returnVal) {
            escrowId = StellarSdk.Address.fromScVal(returnVal).toString();
          }
        }
      }

      if (!escrowId) throw new Error("Could not parse Escrow Contract Address ID from commit transaction.");

      setPostingProgress(prev => ({ ...prev, step: "lock", escrowId }));

      // Step 3.3: Initialize Escrow & Lock Bounty (XLM)
      const accRes3 = await fetch(`${horizonUrl}/accounts/${walletAddress}`);
      const accData3 = await accRes3.json();
      sourceAccount = new StellarSdk.Account(walletAddress, accData3.sequence);

      const amountStroops = BigInt(Math.floor(parseFloat(bountyXlm) * 10000000));
      const textEncoder = new TextEncoder();
      
      // Calculate task SHA-256 hash from description
      const taskHashBuf = Buffer.from(
        new Uint8Array(await crypto.subtle.digest("SHA-256", textEncoder.encode(jobDescription)))
      );

      // args: depositor, provider, token, amount, task_hash, timeout_seconds
      const initArgs = [
        StellarSdk.Address.fromString(walletAddress).toScVal(),
        StellarSdk.Address.fromString(walletAddress).toScVal(),
        StellarSdk.Address.fromString(tokenAddress).toScVal(),
        StellarSdk.nativeToScVal(amountStroops, { type: "i128" }),
        StellarSdk.xdr.ScVal.scvBytes(taskHashBuf),
        StellarSdk.nativeToScVal(BigInt(deadlineSeconds), { type: "u64" })
      ];

      let txInit = new StellarSdk.TransactionBuilder(sourceAccount, { fee: "1000000", networkPassphrase })
        .addOperation(StellarSdk.Operation.invokeContractFunction({
          contract: escrowId,
          function: "initialize",
          args: initArgs
        }))
        .setTimeout(0)
        .build();

      const simInit = await server.simulateTransaction(txInit);
      if ((simInit as any).error) throw new Error(`Escrow initialization simulation failed: ${(simInit as any).error}`);
      txInit = StellarSdk.rpc.assembleTransaction(txInit, simInit).build();
      const initHash = txInit.hash().toString("hex");

      const signInit = await freighter.signTransaction(txInit.toXDR(), { networkPassphrase });
      const signedInit = StellarSdk.TransactionBuilder.fromXDR(signInit.signedTxXdr, networkPassphrase);

      const submitInit = await server.sendTransaction(signedInit);
      if (submitInit.status === "ERROR") throw new Error(`Escrow lock rejected: ${JSON.stringify((submitInit as any).errorResult)}`);

      await pollTx(initHash);

      setPostingProgress(prev => ({ ...prev, step: "register" }));

      // Step 3.4: Post Job to JobBoard
      const accRes4 = await fetch(`${horizonUrl}/accounts/${walletAddress}`);
      const accData4 = await accRes4.json();
      sourceAccount = new StellarSdk.Account(walletAddress, accData4.sequence);

      // Embedded title + description into spec_uri
      const specUriString = `${jobTitle}|${jobDescription}`;

      // args: poster, spec_uri, spec_hash, bounty, token, mode, escrow, deadline
      const postArgs = [
        StellarSdk.Address.fromString(walletAddress).toScVal(),
        StellarSdk.xdr.ScVal.scvBytes(Buffer.from(specUriString, "utf-8")),
        StellarSdk.xdr.ScVal.scvBytes(taskHashBuf),
        StellarSdk.nativeToScVal(amountStroops, { type: "i128" }),
        StellarSdk.Address.fromString(tokenAddress).toScVal(),
        StellarSdk.xdr.ScVal.scvSymbol(mode),
        StellarSdk.Address.fromString(escrowId).toScVal(),
        StellarSdk.nativeToScVal(BigInt(deadlineSeconds), { type: "u64" })
      ];

      let txPost = new StellarSdk.TransactionBuilder(sourceAccount, { fee: "1000000", networkPassphrase })
        .addOperation(StellarSdk.Operation.invokeContractFunction({
          contract: JOB_BOARD_ADDRESS,
          function: "post_job",
          args: postArgs
        }))
        .setTimeout(0)
        .build();

      const simPost = await server.simulateTransaction(txPost);
      if ((simPost as any).error) throw new Error(`JobBoard posting simulation failed: ${(simPost as any).error}`);
      txPost = StellarSdk.rpc.assembleTransaction(txPost, simPost).build();
      const postHash = txPost.hash().toString("hex");

      const signPost = await freighter.signTransaction(txPost.toXDR(), { networkPassphrase });
      const signedPost = StellarSdk.TransactionBuilder.fromXDR(signPost.signedTxXdr, networkPassphrase);

      const submitPost = await server.sendTransaction(signedPost);
      if (submitPost.status === "ERROR") throw new Error(`Bounty Board registration rejected: ${JSON.stringify((submitPost as any).errorResult)}`);

      const postCommit = await pollTx(postHash);
      
      let jobId = 0;
      const retValPost = (postCommit as any).returnValue;
      if (retValPost) {
        jobId = Number(StellarSdk.scValToNative(retValPost));
      }

      setPostingProgress(prev => ({ ...prev, step: "completed", jobId }));
      toast.success("Bounty successfully posted on-chain!");
      
    } catch (err: any) {
      console.error("On-chain flow failed:", err);
      setPostingProgress(prev => ({
        ...prev,
        step: "error",
        errorMsg: err.message || String(err)
      }));
      toast.error(`Posting flow failed: ${err.message || err}`);
    }
  };

  const overlayStyle: React.CSSProperties = {
    position: "fixed", inset: 0, background: "rgba(4, 4, 5, 0.8)", backdropFilter: "blur(16px)",
    display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: "20px",
  };
  const cardStyle: React.CSSProperties = {
    width: "100%", maxWidth: "540px", background: "rgba(10, 10, 12, 0.9)",
    border: "1px solid rgba(255, 255, 255, 0.08)", borderRadius: "12px", padding: "32px",
    boxShadow: "0 20px 50px rgba(0, 0, 0, 0.8)", backdropFilter: "blur(20px)",
  };
  const labelStyle: React.CSSProperties = {
    fontSize: "0.7rem", letterSpacing: "1px", color: "rgba(255,255,255,0.45)",
    textTransform: "uppercase", marginBottom: "6px", display: "block", fontFamily: "var(--font-mono)", fontWeight: "bold",
  };
  const inputStyle: React.CSSProperties = {
    width: "100%", background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)",
    borderRadius: "6px", padding: "10px 14px", color: "#fff", fontSize: "0.9rem", outline: "none",
    transition: "all 0.2s ease",
  };
  const primaryBtnStyle: React.CSSProperties = {
    background: "linear-gradient(135deg, rgba(139, 92, 246, 0.95), rgba(0, 242, 254, 0.95))",
    border: "none", borderRadius: "6px", padding: "10px 20px", color: "#000", fontWeight: 700,
    cursor: "pointer", fontSize: "0.85rem", transition: "all 0.2s ease",
  };
  const ghostBtnStyle: React.CSSProperties = {
    background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)",
    borderRadius: "6px", padding: "10px 20px", color: "#fff", cursor: "pointer", fontSize: "0.85rem",
    transition: "all 0.2s ease",
  };

  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      style={overlayStyle} onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, y: 10 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.95, opacity: 0 }}
        style={cardStyle} onClick={e => e.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <h2 className="font-display" style={{ fontSize: "1.4rem", fontWeight: 800, color: "#fff", margin: 0, letterSpacing: "-0.02em" }}>
            Post a New Bounty Job
          </h2>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "rgba(255,255,255,0.5)", cursor: "pointer", fontSize: "1.2rem" }}>✕</button>
        </div>

        {/* Step indicator */}
        <div style={{ display: "flex", gap: "6px", marginBottom: "22px" }}>
          {[1, 2, 3].map(s => (
            <div key={s} style={{ flex: 1, height: "3px", borderRadius: "2px", background: s <= step ? "var(--accent-cyan)" : "rgba(255,255,255,0.1)" }} />
          ))}
        </div>

        {step === 1 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
            <div>
              <label style={labelStyle}>Job title / task name</label>
              <input 
                style={inputStyle} 
                placeholder="e.g. Build API Indexer Gateway" 
                value={jobTitle}
                onChange={e => setJobTitle(e.target.value)} 
              />
            </div>
            <div>
              <label style={labelStyle}>Job description (spec details)</label>
              <textarea 
                style={{ ...inputStyle, height: "100px", resize: "none" }} 
                placeholder="Describe the exact task requirement. This description string is hashed on-chain to verify agent execution proofs." 
                value={jobDescription}
                onChange={e => setJobDescription(e.target.value)} 
              />
            </div>
            <div>
              <label style={labelStyle}>Execution Mode</label>
              <div style={{ display: "flex", gap: "12px" }}>
                <div 
                  onClick={() => setMode("single")}
                  style={{
                    flex: 1,
                    background: mode === "single" ? "rgba(0, 150, 199, 0.08)" : "rgba(255,255,255,0.02)",
                    border: mode === "single" ? "1.5px solid var(--accent-cyan)" : "1.5px solid rgba(255,255,255,0.08)",
                    borderRadius: "8px",
                    padding: "16px",
                    cursor: "pointer",
                    textAlign: "center",
                    transition: "all 0.2s"
                  }}
                >
                  <Cpu size={24} style={{ color: mode === "single" ? "var(--accent-cyan)" : "rgba(255,255,255,0.4)", marginBottom: "8px" }} />
                  <div style={{ fontSize: "0.85rem", fontWeight: 700, color: "#ffffff" }}>Single Agent</div>
                  <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.4)", marginTop: "4px" }}>One agent claims & settles</div>
                </div>

                <div 
                  onClick={() => setMode("swarm")}
                  style={{
                    flex: 1,
                    background: mode === "swarm" ? "rgba(139, 92, 246, 0.08)" : "rgba(255,255,255,0.02)",
                    border: mode === "swarm" ? "1.5px solid var(--accent-purple)" : "1.5px solid rgba(255,255,255,0.08)",
                    borderRadius: "8px",
                    padding: "16px",
                    cursor: "pointer",
                    textAlign: "center",
                    transition: "all 0.2s"
                  }}
                >
                  <Users size={24} style={{ color: mode === "swarm" ? "var(--accent-purple)" : "rgba(255,255,255,0.4)", marginBottom: "8px" }} />
                  <div style={{ fontSize: "0.85rem", fontWeight: 700, color: "#ffffff" }}>Swarm Mode</div>
                  <div style={{ fontSize: "0.68rem", color: "rgba(255,255,255,0.4)", marginTop: "4px" }}>N agents coordinate splits</div>
                </div>
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", marginTop: "10px" }}>
              <button style={ghostBtnStyle} onClick={onClose}>Cancel</button>
              <button 
                style={{ ...primaryBtnStyle, opacity: isFormValid ? 1 : 0.5 }} 
                disabled={!isFormValid} 
                onClick={() => setStep(2)}
              >
                Next Step
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
            <div>
              <label style={labelStyle}>Bounty lock amount (XLM)</label>
              <input 
                type="number" 
                style={inputStyle} 
                placeholder="10" 
                value={bountyXlm} 
                onChange={e => setBountyXlm(e.target.value)} 
              />
            </div>
            <div>
              <label style={labelStyle}>Settlement payment token (SAC contract)</label>
              <input 
                style={inputStyle} 
                value={tokenAddress} 
                onChange={e => setTokenAddress(e.target.value)} 
              />
              <span style={{ fontSize: "0.64rem", color: "rgba(255,255,255,0.3)" }}>
                Defaults to Stellar testnet native asset contract address.
              </span>
            </div>
            <div>
              <label style={labelStyle}>Refund deadline (seconds)</label>
              <select 
                style={inputStyle} 
                value={deadlineSeconds}
                onChange={e => setDeadlineSeconds(e.target.value)}
              >
                <option value="3600">1 Hour (testing)</option>
                <option value="86400">24 Hours (standard)</option>
                <option value="604800">7 Days</option>
                <option value="2592000">30 Days</option>
              </select>
            </div>

            <div style={{ display: "flex", justifyContent: "space-between", gap: "10px", marginTop: "10px" }}>
              <button style={ghostBtnStyle} onClick={() => setStep(1)}>Back</button>
              <button 
                style={{ ...primaryBtnStyle, opacity: isBountyValid ? 1 : 0.5 }} 
                disabled={!isBountyValid} 
                onClick={runOnChainPostFlow}
              >
                Review & Publish
              </button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
            <h4 style={{ fontSize: "0.95rem", color: "#ffffff", fontWeight: 700, marginBottom: "5px" }}>
              On-Chain Transaction Pipeline
            </h4>
            
            {/* Step progress details */}
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              {[
                { 
                  id: "wasm", 
                  title: "1. Upload Escrow WASM", 
                  desc: "Broadcasting compiled escrow bytecode to Soroban...",
                  active: postingProgress.step === "wasm",
                  done: ["deploy", "lock", "register", "completed"].includes(postingProgress.step)
                },
                { 
                  id: "deploy", 
                  title: "2. Instantiate Escrow Contract", 
                  desc: "Creating isolated on-chain conditional escrow address...",
                  active: postingProgress.step === "deploy",
                  done: ["lock", "register", "completed"].includes(postingProgress.step)
                },
                { 
                  id: "lock", 
                  title: "3. Initialize Escrow & Lock Bounty", 
                  desc: `Locking ${bountyXlm} XLM payout and setting deadlines...`,
                  active: postingProgress.step === "lock",
                  done: ["register", "completed"].includes(postingProgress.step)
                },
                { 
                  id: "register", 
                  title: "4. Register Job on Board", 
                  desc: "Emitting job_posted events and recording coordination spec...",
                  active: postingProgress.step === "register",
                  done: postingProgress.step === "completed"
                }
              ].map(s => {
                let statusColor = "rgba(255,255,255,0.2)";
                let statusIcon = <div style={{ width: "16px", height: "16px", borderRadius: "50%", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.15)" }} />;

                if (s.done) {
                  statusColor = "var(--accent-green)";
                  statusIcon = <Check size={14} style={{ color: "var(--accent-green)" }} />;
                } else if (s.active) {
                  statusColor = "var(--accent-cyan)";
                  statusIcon = <RefreshCw size={14} className="animate-spin" style={{ color: "var(--accent-cyan)" }} />;
                }

                return (
                  <div 
                    key={s.id}
                    style={{
                      background: s.active ? "rgba(0, 150, 199, 0.04)" : "rgba(255,255,255,0.01)",
                      border: s.active ? "1px solid rgba(0, 150, 199, 0.15)" : "1px solid rgba(255, 255, 255, 0.04)",
                      borderRadius: "8px",
                      padding: "12px 16px",
                      display: "flex",
                      gap: "12px",
                      alignItems: "center"
                    }}
                  >
                    <div style={{ flexShrink: 0 }}>{statusIcon}</div>
                    <div>
                      <div style={{ fontSize: "0.85rem", fontWeight: 700, color: statusColor }}>{s.title}</div>
                      <div style={{ fontSize: "0.74rem", color: "rgba(255,255,255,0.4)", marginTop: "2px" }}>{s.desc}</div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Error display */}
            {postingProgress.step === "error" && (
              <div style={{
                background: "rgba(255, 59, 48, 0.05)",
                border: "1px solid rgba(255, 59, 48, 0.2)",
                borderRadius: "8px",
                padding: "16px",
                display: "flex",
                gap: "12px",
                alignItems: "flex-start"
              }}>
                <AlertTriangle size={18} style={{ color: "var(--accent-red)", flexShrink: 0, marginTop: "2px" }} />
                <div>
                  <div style={{ fontSize: "0.85rem", fontWeight: 700, color: "var(--accent-red)" }}>Execution Interrupted</div>
                  <div style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.6)", marginTop: "4px", fontFamily: "var(--font-mono)", wordBreak: "break-all" }}>
                    {postingProgress.errorMsg}
                  </div>
                </div>
              </div>
            )}

            {/* Completion summary */}
            {postingProgress.step === "completed" && (
              <div style={{
                background: "rgba(15, 159, 120, 0.05)",
                border: "1px solid rgba(15, 159, 120, 0.2)",
                borderRadius: "8px",
                padding: "16px"
              }}>
                <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "8px" }}>
                  <Check size={18} style={{ color: "var(--accent-green)" }} />
                  <span style={{ fontSize: "0.9rem", fontWeight: 700, color: "var(--accent-green)" }}>Bounty Registered Successfully!</span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "0.75rem", fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.6)" }}>
                  <div>Job ID: #{postingProgress.jobId}</div>
                  <div>Escrow Contract: {postingProgress.escrowId?.slice(0, 12)}...{postingProgress.escrowId?.slice(-6)}</div>
                  <div style={{ color: "rgba(255,255,255,0.3)" }}>All transactions successfully committed to Soroban Testnet.</div>
                </div>
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px", marginTop: "10px" }}>
              {["error", "completed"].includes(postingProgress.step) ? (
                <button style={primaryBtnStyle} onClick={onClose}>Finish</button>
              ) : (
                <span style={{ fontSize: "0.76rem", color: "rgba(255,255,255,0.3)", display: "flex", alignItems: "center", gap: "6px" }}>
                  <RefreshCw size={12} className="animate-spin" /> Confirm each step in Freighter
                </span>
              )}
            </div>
          </div>
        )}
      </motion.div>
    </motion.div>
  );
}
