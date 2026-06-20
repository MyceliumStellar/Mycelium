"use client";

import React from "react";
import { motion } from "framer-motion";
import { Search, Play, ShieldAlert, ShoppingBag, ArrowRight } from "lucide-react";

interface AgentCardProps {
  title: string;
  role: string;
  icon: React.ReactNode;
  accent: string;
  desc: string;
  steps: string[];
}

function AgentCard({ title, role, icon, accent, desc, steps }: AgentCardProps) {
  return (
    <motion.div
      whileHover={{ y: -6, scale: 1.01 }}
      transition={{ type: "spring", stiffness: 300, damping: 20 }}
      className="premium-card"
      style={{
        display: "flex",
        flexDirection: "column",
        padding: "28px 24px",
        borderRadius: "12px",
        height: "100%",
        cursor: "pointer",
        position: "relative"
      }}
    >
      {/* Decorative colored glow on top left */}
      <div 
        style={{
          position: "absolute",
          top: "-48px",
          left: "-48px",
          width: "96px",
          height: "96px",
          borderRadius: "50%",
          opacity: 0.15,
          filter: "blur(24px)",
          backgroundColor: accent,
          pointerEvents: "none"
        }}
      />

      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        marginBottom: "20px",
        position: "relative",
        zIndex: 10
      }}>
        <div style={{
          padding: "10px",
          borderRadius: "8px",
          border: `1px solid ${accent}40`,
          background: "rgba(255, 255, 255, 0.02)",
          color: accent,
          display: "flex",
          alignItems: "center",
          justifyContent: "center"
        }}>
          {icon}
        </div>
        <span style={{
          marginLeft: "auto",
          fontSize: "0.65rem",
          fontFamily: "var(--font-mono)",
          color: "rgba(255, 255, 255, 0.5)",
          textTransform: "uppercase",
          letterSpacing: "1.5px",
          fontWeight: "bold"
        }}>
          {role}
        </span>
      </div>

      <h3 className="font-display" style={{
        fontSize: "1.25rem",
        fontWeight: "bold",
        color: "var(--foreground)",
        marginBottom: "10px",
        position: "relative",
        zIndex: 10
      }}>
        {title}
      </h3>
      
      <p style={{
        fontSize: "0.85rem",
        color: "rgba(255, 255, 255, 0.6)",
        marginBottom: "24px",
        flexGrow: 1,
        fontFamily: "var(--font-sans)",
        fontWeight: 300,
        lineHeight: "1.6"
      }}>
        {desc}
      </p>

      {/* Steps checklist */}
      <div style={{
        borderTop: "1px solid rgba(255, 255, 255, 0.08)",
        paddingTop: "16px",
        marginTop: "auto"
      }}>
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {steps.map((step, idx) => (
            <li key={idx} style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              fontSize: "0.75rem",
              fontFamily: "var(--font-mono)",
              color: "rgba(255, 255, 255, 0.5)",
              marginBottom: idx === steps.length - 1 ? 0 : "8px"
            }}>
              <span style={{
                width: "6px",
                height: "6px",
                borderRadius: "50%",
                backgroundColor: accent
              }}></span>
              <span>{step}</span>
            </li>
          ))}
        </ul>
      </div>
    </motion.div>
  );
}

export default function AgentArchitecture() {
  const agents = [
    {
      title: "Research Agent",
      role: "Discover & Query",
      icon: <Search size={18} />,
      accent: "var(--accent-cyan)",
      desc: "Aggregates liquidity feeds, queries on-chain orderbooks, and searches Stellar contracts for coordination endpoints.",
      steps: ["Scans DEX liquidity pools", "Queries contract registries", "Fetches arbitrage routes"]
    },
    {
      title: "Execution Agent",
      role: "Process & Compile",
      icon: <Play size={18} />,
      accent: "#c084fc",
      desc: "Executes state transitions, processes multi-contract calls statelessly, and compiles Python scripts into sandboxed bytecode.",
      steps: ["Loads Python agent script", "Compiles Soroban WASM", "Verifies runtime security"]
    },
    {
      title: "Treasury Agent",
      role: "Store & Escrow",
      icon: <ShieldAlert size={18} />,
      accent: "var(--accent-purple)",
      desc: "Escrows digital tokens, manages cryptographic multisig setups, and securely holds liquidity reserves under smart control.",
      steps: ["Enforces multisig policies", "Locks collateral reserves", "Signs Soroban transactions"]
    },
    {
      title: "Commerce Agent",
      role: "Settle & Transact",
      icon: <ShoppingBag size={18} />,
      accent: "var(--accent-green)",
      desc: "Coordinates microtransactions with external counterparties, clears Stellar payments, and manages autonomous agent billing.",
      steps: ["Triggers Stellar payments", "Signs payment channels", "Issues atomic settlements"]
    }
  ];

  return (
    <div style={{ width: "100%", maxWidth: "1200px", margin: "0 auto" }}>
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))",
        gap: "24px",
        position: "relative"
      }}>
        {agents.map((agent, index) => (
          <React.Fragment key={index}>
            <AgentCard
              title={agent.title}
              role={agent.role}
              icon={agent.icon}
              accent={agent.accent}
              desc={agent.desc}
              steps={agent.steps}
            />
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
