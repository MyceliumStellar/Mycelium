"use client";

import React from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  Code2,
  Search,
  Coins,
  Terminal as CliIcon,
  Laptop,
  Layers,
  ArrowRight,
  ExternalLink,
  ChevronRight
} from "lucide-react";
import InteractiveTerminal from "../components/InteractiveTerminal";
import AgentArchitecture from "../components/AgentArchitecture";

const ease = [0.22, 1, 0.36, 1] as const;

// Standard scroll-reveal variant
const inView = {
  hidden: { opacity: 0, y: 22 },
  show:   { opacity: 1, y: 0  }
};

const inViewTransition = (delay = 0) => ({
  duration: 0.55,
  delay,
  ease
});

export default function Home() {
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
      {/* Subtle grid */}
      <div className="premium-grid" style={{
        position: "fixed",
        top: 0, left: 0, right: 0, bottom: 0,
        pointerEvents: "none",
        zIndex: 0
      }} />

      {/* Orb — CSS orb-breathe keyframe handles the pulse */}
      <div className="glow-orb-cyan" style={{
        position: "absolute",
        top: "-80px",
        left: "50%",
        transform: "translateX(-50%)",
        width: "700px",
        height: "500px",
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
          <Link href="/" style={{ display: "flex", alignItems: "center", color: "var(--foreground)" }}>
            <span className="font-display" style={{ fontSize: "1.2rem", fontWeight: 800, letterSpacing: "-0.04em" }}>
              Mycelium
            </span>
          </Link>

          <nav style={{ display: "none", gap: "28px" }} className="md-nav-links">
            <a href="#features"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >features</a>
            <a href="#architecture"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >architecture</a>
            <a href="https://github.com" target="_blank" rel="noopener noreferrer"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", display: "flex", alignItems: "center", gap: "4px" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >docs <ExternalLink size={11} /></a>
          </nav>
          <style jsx>{`
            @media (min-width: 768px) {
              .md-nav-links { display: flex !important; }
            }
          `}</style>

          <Link href="/playground" className="premium-button-primary" style={{
            padding: "7px 16px",
            fontSize: "0.76rem",
            borderRadius: "6px"
          }}>
            Launch Playground
          </Link>
        </div>
      </header>

      {/* ─── Hero ─── */}
      <section style={{
        position: "relative",
        zIndex: 10,
        padding: "112px 24px 72px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center"
      }}>
        <div style={{ maxWidth: "860px", margin: "0 auto" }}>

          {/* Tag pill */}
          <motion.div
            initial={{ opacity: 0, y: -14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease }}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "8px",
              background: "rgba(255,255,255,0.03)",
              border: "1px solid rgba(255,255,255,0.08)",
              padding: "5px 14px",
              borderRadius: "20px",
              marginBottom: "36px"
            }}
          >
            <span style={{
              width: "5px", height: "5px",
              borderRadius: "50%",
              backgroundColor: "var(--accent-cyan)",
              animation: "pulse-cyan-purple 2s infinite"
            }} />
            <span style={{
              fontSize: "0.68rem",
              fontFamily: "var(--font-mono)",
              color: "rgba(255,255,255,0.45)",
              letterSpacing: "2px"
            }}>
              STELLAR SOROBAN AGENT SDK
            </span>
          </motion.div>

          {/* H1 */}
          <motion.h1
            initial={{ opacity: 0, y: 22 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.65, delay: 0.1, ease }}
            style={{
              fontSize: "clamp(3rem, 7.5vw, 5.8rem)",
              fontWeight: 800,
              lineHeight: "1.04",
              letterSpacing: "-0.055em",
              color: "#ffffff",
              marginBottom: "36px",
              fontFamily: "var(--font-display)"
            }}
          >
            Every Agent Needs<br />a Wallet.
          </motion.h1>

          {/* Editorial metadata divider */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.6, delay: 0.26, ease }}
            className="divider-label"
            style={{ maxWidth: "560px", margin: "0 auto 32px" }}
          >
            <span>Python-first</span>
            <span>Stellar Soroban</span>
            <span>v0.1.0-alpha</span>
          </motion.div>

          {/* Subheadline */}
          <motion.h2
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.35, ease }}
            style={{
              fontSize: "clamp(1rem, 2.2vw, 1.2rem)",
              color: "rgba(255,255,255,0.6)",
              lineHeight: "1.65",
              maxWidth: "620px",
              margin: "0 auto 44px",
              fontWeight: 400
            }}
          >
            The Python-first framework for creating agents that discover, coordinate, and transact on Stellar.
          </motion.h2>

          {/* CTAs */}
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.46, ease }}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "12px",
              marginBottom: "80px"
            }}
          >
            <Link href="/playground" className="premium-button-primary">
              Launch Playground
              <ChevronRight size={15} />
            </Link>
            <a href="https://github.com" target="_blank" rel="noopener noreferrer" className="premium-button-secondary">
              Read SDK Docs
            </a>
          </motion.div>
        </div>

        {/* Terminal — rises up after CTAs */}
        <motion.div
          initial={{ opacity: 0, y: 36 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.62, ease }}
          style={{ width: "100%", padding: "0 12px", position: "relative", zIndex: 20 }}
        >
          <InteractiveTerminal />
        </motion.div>
      </section>

      {/* ─── Agent Architecture ─── */}
      <section id="architecture" style={{
        position: "relative",
        zIndex: 10,
        padding: "100px 24px",
        borderTop: "1px solid rgba(255,255,255,0.06)"
      }}>
        <div style={{ maxWidth: "1200px", margin: "0 auto" }}>
          <motion.div
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, amount: 0.3 }}
            variants={inView}
            transition={inViewTransition()}
            style={{ textAlign: "center", marginBottom: "56px" }}
          >
            <span style={{
              fontSize: "0.68rem",
              fontFamily: "var(--font-mono)",
              color: "var(--accent-purple)",
              textTransform: "uppercase",
              letterSpacing: "3px",
              fontWeight: "bold",
              display: "block",
              marginBottom: "18px"
            }}>
              AGENT STACK
            </span>
            <h2 className="font-display" style={{
              fontSize: "clamp(1.9rem, 5vw, 3rem)",
              fontWeight: 800,
              color: "#ffffff",
              letterSpacing: "-0.045em",
              lineHeight: "1.1"
            }}>
              One agent is useful.
              <br />
              <span className="font-serif" style={{ fontStyle: "italic", fontWeight: "normal" }}>A network of agents</span> changes everything.
            </h2>
            <p style={{
              fontSize: "0.95rem",
              color: "rgba(255,255,255,0.45)",
              maxWidth: "500px",
              margin: "16px auto 0",
              fontWeight: 300,
              lineHeight: "1.7"
            }}>
              Together they form autonomous economic systems.
            </p>
          </motion.div>

          <AgentArchitecture />
        </div>
      </section>

      {/* ─── Features Bento ─── */}
      <section id="features" style={{
        position: "relative",
        zIndex: 10,
        padding: "100px 24px",
        borderTop: "1px solid rgba(255,255,255,0.06)"
      }}>
        <div style={{ maxWidth: "1200px", margin: "0 auto" }}>
          <motion.div
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, amount: 0.3 }}
            variants={inView}
            transition={inViewTransition()}
            style={{ textAlign: "center", marginBottom: "60px" }}
          >
            <span style={{
              fontSize: "0.68rem",
              fontFamily: "var(--font-mono)",
              color: "var(--accent-cyan)",
              textTransform: "uppercase",
              letterSpacing: "3px",
              fontWeight: "bold",
              display: "block",
              marginBottom: "16px"
            }}>
              FEATURES
            </span>
            <h2 className="font-display" style={{
              fontSize: "clamp(1.8rem, 4vw, 2.4rem)",
              fontWeight: 700,
              color: "#ffffff",
              letterSpacing: "-0.04em"
            }}>
              A Complete Toolkit for Autonomous Agents
            </h2>
            <p style={{
              fontSize: "0.88rem",
              color: "rgba(255,255,255,0.4)",
              maxWidth: "460px",
              margin: "12px auto 0",
              fontWeight: 300,
              lineHeight: "1.7"
            }}>
              Everything you need to build, test, and deploy smart contract pipelines on the Stellar Soroban network.
            </p>
          </motion.div>

          {/* ── Bento grid ── */}
          <div className="bento-feature-grid">

            {/* Python First — wide, row 1 cols 1-2 */}
            <motion.div
              className="card-flat card-accent-cyan bento-python"
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, amount: 0.15 }}
              variants={inView}
              transition={inViewTransition(0)}
              whileHover={{ y: -6, transition: { type: "spring", stiffness: 320, damping: 22 } }}
              style={{ padding: "36px" }}
            >
              <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "18px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ color: "var(--accent-cyan)" }}><Code2 size={20} /></div>
                  <span style={{ fontSize: "0.6rem", fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.18)", letterSpacing: "2px" }}>01</span>
                </div>
                <div>
                  <h3 className="font-display" style={{ fontSize: "1.45rem", fontWeight: 700, color: "#fff", letterSpacing: "-0.03em", marginBottom: "5px" }}>
                    Python First
                  </h3>
                  <div style={{ fontSize: "0.68rem", fontFamily: "var(--font-mono)", color: "var(--accent-cyan)", letterSpacing: "0.5px" }}>
                    Build in Python. Deploy to Stellar.
                  </div>
                </div>
                <p style={{ fontSize: "0.85rem", color: "rgba(255,255,255,0.5)", lineHeight: "1.7", fontWeight: 300, flexGrow: 1 }}>
                  Mycelium removes the complexity of blockchain development by allowing developers to create autonomous agents using the language they already know.
                </p>
                <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                  {["Write logic.", "Deploy agents.", "Scale economies."].map((item, i) => (
                    <span key={i} className="tag-chip tag-chip-cyan">{item}</span>
                  ))}
                </div>
              </div>
            </motion.div>

            {/* CLI — tall, col 3, rows 1-2 */}
            <motion.div
              className="card-terminal bento-cli"
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, amount: 0.1 }}
              variants={inView}
              transition={inViewTransition(0.08)}
              whileHover={{ y: -6, transition: { type: "spring", stiffness: 320, damping: 22 } }}
              style={{ padding: "30px" }}
            >
              <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "20px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ color: "var(--accent-cyan)" }}><CliIcon size={18} /></div>
                  <span style={{ fontSize: "0.6rem", fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.15)", letterSpacing: "2px" }}>06</span>
                </div>
                <div>
                  <h3 className="font-display" style={{ fontSize: "1.15rem", fontWeight: 700, color: "#fff", letterSpacing: "-0.025em", marginBottom: "4px" }}>
                    CLI Section
                  </h3>
                  <div style={{ fontSize: "0.65rem", fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.3)", letterSpacing: "0.5px" }}>
                    Built For The Command Line.
                  </div>
                </div>
                <p style={{ fontSize: "0.8rem", color: "rgba(255,255,255,0.4)", lineHeight: "1.65", fontWeight: 300 }}>
                  From project creation to deployment. A complete workflow for autonomous agent development.
                </p>
                <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: "8px" }}>
                  {["mycelium init", "mycelium create", "mycelium deploy", "mycelium monitor"].map((cmd, i) => (
                    <motion.div
                      key={i}
                      initial={{ opacity: 0, x: -8 }}
                      whileInView={{ opacity: 1, x: 0 }}
                      viewport={{ once: true }}
                      transition={{ duration: 0.35, delay: 0.3 + i * 0.07, ease }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "10px",
                        fontFamily: "var(--font-mono)",
                        fontSize: "0.8rem",
                        padding: "9px 12px",
                        background: "rgba(255,255,255,0.03)",
                        borderRadius: "6px",
                        border: "1px solid rgba(255,255,255,0.06)"
                      }}
                    >
                      <span style={{ color: "var(--accent-cyan)", userSelect: "none", fontWeight: "bold" }}>$</span>
                      <span style={{ color: "rgba(255,255,255,0.65)" }}>{cmd}</span>
                    </motion.div>
                  ))}
                </div>
              </div>
            </motion.div>

            {/* Agent Registry — col 1, row 2 */}
            <motion.div
              className="card-flat card-accent-purple bento-registry"
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, amount: 0.2 }}
              variants={inView}
              transition={inViewTransition(0.14)}
              whileHover={{ y: -5, transition: { type: "spring", stiffness: 320, damping: 22 } }}
              style={{ padding: "28px" }}
            >
              <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "14px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ color: "var(--accent-purple)" }}><Search size={18} /></div>
                  <span style={{ fontSize: "0.6rem", fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.18)", letterSpacing: "2px" }}>02</span>
                </div>
                <div>
                  <h3 className="font-display" style={{ fontSize: "1.1rem", fontWeight: 700, color: "#fff", letterSpacing: "-0.025em", marginBottom: "4px" }}>
                    Agent Registry
                  </h3>
                  <div style={{ fontSize: "0.65rem", fontFamily: "var(--font-mono)", color: "var(--accent-purple)", letterSpacing: "0.5px" }}>
                    Discover Agents. Every agent has an identity.
                  </div>
                </div>
                <p style={{ fontSize: "0.8rem", color: "rgba(255,255,255,0.45)", lineHeight: "1.65", fontWeight: 300, flexGrow: 1 }}>
                  The registry transforms isolated agents into an interconnected ecosystem.
                </p>
                <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "6px" }}>
                  {["Register capabilities.", "Build reputation.", "Offer services.", "Find collaborators."].map((item, i) => (
                    <li key={i} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.72rem", color: "rgba(255,255,255,0.38)" }}>
                      <span style={{ width: "3px", height: "3px", borderRadius: "50%", backgroundColor: "var(--accent-purple)", flexShrink: 0 }} />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </motion.div>

            {/* Agent Commerce — col 2, row 2 */}
            <motion.div
              className="card-flat bento-commerce"
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, amount: 0.2 }}
              variants={inView}
              transition={inViewTransition(0.2)}
              whileHover={{ y: -5, transition: { type: "spring", stiffness: 320, damping: 22 } }}
              style={{ padding: "28px" }}
            >
              <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "14px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ color: "var(--accent-cyan)" }}><Coins size={18} /></div>
                  <span style={{ fontSize: "0.6rem", fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.18)", letterSpacing: "2px" }}>03</span>
                </div>
                <div>
                  <h3 className="font-display" style={{ fontSize: "1.1rem", fontWeight: 700, color: "#fff", letterSpacing: "-0.025em", marginBottom: "4px" }}>
                    Agent Commerce
                  </h3>
                  <div style={{ fontSize: "0.65rem", fontFamily: "var(--font-mono)", color: "var(--accent-cyan)", letterSpacing: "0.5px" }}>
                    Software can now participate in the economy.
                  </div>
                </div>
                <p style={{ fontSize: "0.8rem", color: "rgba(255,255,255,0.45)", lineHeight: "1.65", fontWeight: 300, flexGrow: 1 }}>
                  Agents can request services, exchange information, purchase resources, and settle payments autonomously.
                </p>
                <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "6px" }}>
                  {["Research.", "Negotiate.", "Transact.", "Without human intervention."].map((item, i) => (
                    <li key={i} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.72rem", color: "rgba(255,255,255,0.38)" }}>
                      <span style={{ width: "3px", height: "3px", borderRadius: "50%", backgroundColor: "var(--accent-cyan)", flexShrink: 0 }} />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </motion.div>

            {/* Playground — col 1, row 3 */}
            <motion.div
              className="card-flat card-accent-purple bento-playground"
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, amount: 0.2 }}
              variants={inView}
              transition={inViewTransition(0.08)}
              whileHover={{ y: -5, transition: { type: "spring", stiffness: 320, damping: 22 } }}
              style={{ padding: "28px" }}
            >
              <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "14px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ color: "var(--accent-purple)" }}><Laptop size={18} /></div>
                  <span style={{ fontSize: "0.6rem", fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.18)", letterSpacing: "2px" }}>04</span>
                </div>
                <div>
                  <h3 className="font-display" style={{ fontSize: "1.1rem", fontWeight: 700, color: "#fff", letterSpacing: "-0.025em", marginBottom: "4px" }}>
                    Playground
                  </h3>
                  <div style={{ fontSize: "0.65rem", fontFamily: "var(--font-mono)", color: "var(--accent-purple)", letterSpacing: "0.5px" }}>
                    Start Building in Minutes.
                  </div>
                </div>
                <p style={{ fontSize: "0.8rem", color: "rgba(255,255,255,0.45)", lineHeight: "1.65", fontWeight: 300, flexGrow: 1 }}>
                  Experiment with agents directly in the browser. No setup required.
                </p>
                <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "6px" }}>
                  {["Create agents.", "Test workflows.", "Deploy prototypes.", "Explore autonomous systems."].map((item, i) => (
                    <li key={i} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.72rem", color: "rgba(255,255,255,0.38)" }}>
                      <span style={{ width: "3px", height: "3px", borderRadius: "50%", backgroundColor: "var(--accent-purple)", flexShrink: 0 }} />
                      {item}
                    </li>
                  ))}
                </ul>
                <div style={{ marginTop: "auto", paddingTop: "8px" }}>
                  <Link href="/playground" style={{
                    display: "inline-flex", alignItems: "center", gap: "4px",
                    fontSize: "0.78rem", fontWeight: 600, color: "var(--accent-purple)"
                  }}>
                    Open Playground <ArrowRight size={12} />
                  </Link>
                </div>
              </div>
            </motion.div>

            {/* SDK Section — wide horizontal, col 2-3, row 3 */}
            <motion.div
              className="card-flat bento-sdk"
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, amount: 0.2 }}
              variants={inView}
              transition={inViewTransition(0.16)}
              whileHover={{ y: -5, transition: { type: "spring", stiffness: 320, damping: 22 } }}
              style={{ padding: "28px 36px" }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: "36px", height: "100%", flexWrap: "wrap" }}>
                <div style={{ minWidth: "140px" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "14px" }}>
                    <div style={{ color: "var(--accent-cyan)" }}><Layers size={18} /></div>
                    <span style={{ fontSize: "0.6rem", fontFamily: "var(--font-mono)", color: "rgba(255,255,255,0.18)", letterSpacing: "2px" }}>05</span>
                  </div>
                  <h3 className="font-display" style={{ fontSize: "1.1rem", fontWeight: 700, color: "#fff", letterSpacing: "-0.025em", marginBottom: "4px" }}>
                    SDK Section
                  </h3>
                  <div style={{ fontSize: "0.65rem", fontFamily: "var(--font-mono)", color: "var(--accent-cyan)", letterSpacing: "0.5px" }}>
                    Designed for Developers.
                  </div>
                </div>
                <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "14px", justifyContent: "center" }}>
                  <p style={{ fontSize: "0.82rem", color: "rgba(255,255,255,0.45)", lineHeight: "1.7", fontWeight: 300 }}>
                    A clean, powerful SDK for building autonomous agents. Everything from Python.
                  </p>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "7px" }}>
                    {["Create agents.", "Manage wallets.", "Coordinate workflows.", "Deploy on Stellar."].map((item, i) => (
                      <span key={i} className="tag-chip">{item}</span>
                    ))}
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center" }}>
                  <a href="https://github.com" target="_blank" rel="noopener noreferrer" style={{
                    display: "inline-flex", alignItems: "center", gap: "4px",
                    fontSize: "0.78rem", fontWeight: 600, color: "var(--accent-cyan)",
                    whiteSpace: "nowrap"
                  }}>
                    Explore SDK Docs <ArrowRight size={12} />
                  </a>
                </div>
              </div>
            </motion.div>

          </div>
        </div>
      </section>

      {/* ─── Vision ─── */}
      <section style={{
        position: "relative",
        zIndex: 10,
        padding: "140px 24px",
        textAlign: "center",
        borderTop: "1px solid rgba(255,255,255,0.06)"
      }}>
        <div style={{ maxWidth: "800px", margin: "0 auto" }}>
          <motion.div
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, amount: 0.2 }}
            variants={inView}
            transition={inViewTransition()}
            className="divider-label"
            style={{ marginBottom: "52px" }}
          >
            <span>Vision Statement</span>
          </motion.div>

          <motion.h2
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, amount: 0.2 }}
            variants={inView}
            transition={inViewTransition(0.1)}
            className="font-display"
            style={{
              fontSize: "clamp(2.2rem, 5.5vw, 4rem)",
              fontWeight: 800,
              lineHeight: "1.08",
              marginBottom: "44px",
              letterSpacing: "-0.055em",
              color: "#ffffff"
            }}
          >
            A New Economic Species<br />Is Emerging.
          </motion.h2>

          <motion.div
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, amount: 0.2 }}
            variants={inView}
            transition={inViewTransition(0.2)}
            style={{
              fontSize: "clamp(1.1rem, 2.5vw, 1.45rem)",
              color: "rgba(255,255,255,0.7)",
              lineHeight: "1.75",
              marginBottom: "40px",
              fontWeight: 300,
              fontFamily: "var(--font-serif)",
              fontStyle: "italic",
              maxWidth: "660px",
              margin: "0 auto 40px"
            }}
          >
            "For centuries, software executed instructions.
            <br />
            Now software can discover, coordinate, and transact."
          </motion.div>

          <motion.p
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, amount: 0.2 }}
            variants={inView}
            transition={inViewTransition(0.3)}
            style={{
              fontSize: "clamp(0.9rem, 2vw, 1.05rem)",
              color: "rgba(255,255,255,0.38)",
              lineHeight: "1.7",
              maxWidth: "540px",
              margin: "0 auto 52px",
              fontWeight: 300
            }}
          >
            The next economy will not be built solely by humans. It will be built by autonomous agents.
          </motion.p>

          <motion.div
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, amount: 0.2 }}
            variants={inView}
            transition={inViewTransition(0.38)}
            style={{ display: "flex", justifyContent: "center", gap: "12px" }}
          >
            <Link href="/playground" className="premium-button-primary">
              Launch Playground
              <ChevronRight size={15} />
            </Link>
            <a href="https://github.com" target="_blank" rel="noopener noreferrer" className="premium-button-secondary">
              Read SDK Docs
            </a>
          </motion.div>
        </div>
      </section>

      {/* ─── Footer ─── */}
      <footer style={{
        position: "relative",
        zIndex: 10,
        borderTop: "1px solid rgba(255,255,255,0.06)",
        padding: "48px 24px"
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
              <span>v0.1.0-alpha</span>
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
              <a href="https://github.com" target="_blank" rel="noopener noreferrer"
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
