"use client";

import React from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import {
  Code2,
  Search,
  Coins,
  Terminal as CliIcon,
  Laptop,
  Layers,
  Cpu,
  ArrowRight,
  ExternalLink,
  ChevronRight
} from "lucide-react";
import InteractiveTerminal from "../components/InteractiveTerminal";
import AgentArchitecture from "../components/AgentArchitecture";

// Load Three.js background dynamically with SSR disabled to prevent pre-render crashes
const MyceliumNetwork = dynamic(() => import("../components/MyceliumNetwork"), {
  ssr: false,
});

// Inline monochrome SVG illustrations mimicking the greyscale photo inserts in the screenshots
const SporesSVG = () => (
  <span style={{
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: "80px",
    height: "32px",
    background: "#0c0b0d",
    borderRadius: "16px",
    margin: "0 8px",
    verticalAlign: "middle",
    overflow: "hidden",
    border: "1px solid rgba(255, 255, 255, 0.08)",
    boxShadow: "0 4px 10px rgba(0,0,0,0.15)"
  }}>
    <svg width="80" height="32" viewBox="0 0 80 32" fill="none">
      <circle cx="15" cy="10" r="1.2" fill="#ffffff" opacity="0.2" />
      <circle cx="40" cy="20" r="1.8" fill="#ffffff" opacity="0.4" />
      <circle cx="65" cy="8" r="1.2" fill="#ffffff" opacity="0.2" />
      <circle cx="25" cy="24" r="0.8" fill="#ffffff" opacity="0.15" />
      <line x1="15" y1="10" x2="40" y2="20" stroke="#ffffff" strokeWidth="0.5" opacity="0.15" />
      <line x1="40" y1="20" x2="65" y2="8" stroke="#ffffff" strokeWidth="0.5" opacity="0.2" />
      <circle cx="25" cy="15" r="1.2" fill="var(--accent-cyan)" />
      <circle cx="50" cy="14" r="1.2" fill="var(--accent-purple)" />
    </svg>
  </span>
);

const ChartSVG = () => (
  <span style={{
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: "96px",
    height: "32px",
    background: "#0c0b0d",
    borderRadius: "16px",
    margin: "0 8px",
    verticalAlign: "middle",
    overflow: "hidden",
    border: "1px solid rgba(255, 255, 255, 0.08)",
    boxShadow: "0 4px 10px rgba(0,0,0,0.15)"
  }}>
    <svg width="96" height="32" viewBox="0 0 96 32" fill="none">
      <path d="M8 22 L24 12 L44 18 L64 8 L88 10" stroke="var(--accent-cyan)" strokeWidth="0.85" opacity="0.75" />
      <text x="10" y="9" fill="#ffffff" opacity="0.35" fontSize="5.5" fontFamily="monospace">SOROBAN/XLM</text>
      <text x="54" y="26" fill="#ffffff" opacity="0.5" fontSize="5.5" fontFamily="monospace">1.2s LATENCY</text>
      <line x1="8" y1="28" x2="88" y2="28" stroke="#ffffff" strokeWidth="0.5" opacity="0.1" />
    </svg>
  </span>
);

const CodeSVG = () => (
  <span style={{
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: "100px",
    height: "48px",
    background: "#0c0b0d",
    borderRadius: "6px",
    margin: "0 10px",
    verticalAlign: "middle",
    overflow: "hidden",
    boxShadow: "0 4px 10px rgba(0,0,0,0.15)"
  }}>
    <svg width="100" height="48" viewBox="0 0 100 48" fill="none">
      <text x="8" y="18" fill="var(--accent-purple)" fontSize="6.5" fontFamily="monospace">@contract</text>
      <text x="8" y="28" fill="#ffffff" opacity="0.8" fontSize="6.5" fontFamily="monospace">class Agent:</text>
      <text x="16" y="38" fill="var(--accent-cyan)" fontSize="6.5" fontFamily="monospace">def execute()</text>
    </svg>
  </span>
);

export default function Home() {
  const features = [
    {
      title: "Python First",
      subtitle: "Build in Python. Deploy to Stellar.",
      icon: <Code2 size={20} />,
      desc: "Mycelium removes the complexity of blockchain development by allowing developers to create autonomous agents using the language they already know.",
      list: ["Write logic.", "Deploy agents.", "Scale economies."]
    },
    {
      title: "Agent Registry",
      subtitle: "Discover Agents. Every agent has an identity.",
      icon: <Search size={20} />,
      desc: "The registry transforms isolated agents into an interconnected ecosystem.",
      list: ["Register capabilities.", "Build reputation.", "Offer services.", "Find collaborators."]
    },
    {
      title: "Agent Commerce",
      subtitle: "Software can now participate in the economy.",
      icon: <Coins size={20} />,
      desc: "Agents can request services, exchange information, purchase resources, and settle payments autonomously.",
      list: ["Research.", "Negotiate.", "Transact.", "Without human intervention."]
    },
    {
      title: "Playground",
      subtitle: "Start Building in Minutes.",
      icon: <Laptop size={20} />,
      desc: "Experiment with agents directly in the browser. No setup required.",
      list: ["Create agents.", "Test workflows.", "Deploy prototypes.", "Explore autonomous systems."],
      buttonText: "Open Playground",
      buttonLink: "/playground"
    },
    {
      title: "SDK Section",
      subtitle: "Designed for Developers.",
      icon: <Layers size={20} />,
      desc: "A clean, powerful SDK for building autonomous agents. Everything from Python.",
      list: ["Create agents.", "Manage wallets.", "Coordinate workflows.", "Deploy on Stellar."],
      buttonText: "Explore SDK Docs",
      buttonLink: "https://github.com"
    },
    {
      title: "CLI Section",
      subtitle: "Built For The Command Line.",
      icon: <CliIcon size={20} />,
      desc: "From project creation to deployment. A complete workflow for autonomous agent development.",
      list: ["mycelium init", "mycelium create", "mycelium deploy", "mycelium monitor"],
      isCli: true
    }
  ];

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
      {/* Background Interactive Mycelium Network Canvas */}
      {/* <MyceliumNetwork /> */}

      {/* Grid Overlay for Premium Depth */}
      <div className="premium-grid" style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        pointerEvents: "none",
        zIndex: 1
      }} />

      {/* Decorative Light Blur Orbs */}
      <div className="glow-orb-cyan" style={{
        position: "absolute",
        top: "10%",
        left: "5%",
        width: "500px",
        height: "500px",
        pointerEvents: "none",
        zIndex: 1
      }} />
      <div className="glow-orb-purple" style={{
        position: "absolute",
        top: "40%",
        right: "5%",
        width: "600px",
        height: "600px",
        pointerEvents: "none",
        zIndex: 1
      }} />

      {/* Main Header / Navigation */}
      <header style={{
        position: "sticky",
        top: 0,
        zIndex: 100,
        background: "rgba(4, 4, 5, 0.75)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        borderBottom: "1px solid rgba(255, 255, 255, 0.08)"
      }}>
        <div style={{
          maxWidth: "1200px",
          margin: "0 auto",
          padding: "18px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between"
        }}>
          {/* Logo */}
          <Link href="/" style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            color: "var(--foreground)"
          }}>
            <span className="font-display" style={{
              fontSize: "1.35rem",
              fontWeight: 800,
              letterSpacing: "-0.04em",
              textShadow: "none"
            }}>
              Mycelium
            </span>
          </Link>

          {/* Navigation Links */}
          <nav style={{
            display: "none",
            gap: "28px"
          }} className="md-nav-links">
            <a href="#features" style={{ fontSize: "0.85rem", color: "rgba(255, 255, 255, 0.6)", transition: "color 0.2s" }} onMouseEnter={(e) => e.currentTarget.style.color = "#ffffff"} onMouseLeave={(e) => e.currentTarget.style.color = "rgba(255, 255, 255, 0.6)"}>features</a>
            <a href="#architecture" style={{ fontSize: "0.85rem", color: "rgba(255, 255, 255, 0.6)", transition: "color 0.2s" }} onMouseEnter={(e) => e.currentTarget.style.color = "#ffffff"} onMouseLeave={(e) => e.currentTarget.style.color = "rgba(255, 255, 255, 0.6)"}>architecture</a>
            <a href="#registry" style={{ fontSize: "0.85rem", color: "rgba(255, 255, 255, 0.6)", transition: "color 0.2s" }} onMouseEnter={(e) => e.currentTarget.style.color = "#ffffff"} onMouseLeave={(e) => e.currentTarget.style.color = "rgba(255, 255, 255, 0.6)"}>registry</a>
            <a href="https://github.com" target="_blank" rel="noopener noreferrer" style={{ fontSize: "0.85rem", color: "rgba(255, 255, 255, 0.6)", display: "flex", alignItems: "center", gap: "4px" }} onMouseEnter={(e) => e.currentTarget.style.color = "#ffffff"} onMouseLeave={(e) => e.currentTarget.style.color = "rgba(255, 255, 255, 0.6)"}>docs <ExternalLink size={11} /></a>
          </nav>
          <style jsx>{`
            @media (min-width: 768px) {
              .md-nav-links {
                display: flex !important;
              }
            }
          `}</style>

          {/* Header Action Button */}
          <div>
            <Link href="/playground" className="premium-button-primary" style={{
              padding: "8px 16px",
              fontSize: "0.8rem",
              borderRadius: "6px"
            }}>
              Launch Playground
            </Link>
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <section style={{
        position: "relative",
        zIndex: 10,
        padding: "100px 24px 80px 24px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center"
      }}>
        <div style={{ maxWidth: "1000px", margin: "0 auto" }}>
          {/* pre-headline tag */}
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "8px",
            background: "rgba(255, 255, 255, 0.03)",
            border: "1px solid rgba(255, 255, 255, 0.08)",
            padding: "6px 14px",
            borderRadius: "20px",
            marginBottom: "28px"
          }}>
            <span style={{
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              backgroundColor: "var(--accent-cyan)",
              animation: "pulse-cyan-purple 2s infinite"
            }}></span>
            <span style={{ fontSize: "0.75rem", fontFamily: "var(--font-mono)", color: "rgba(255, 255, 255, 0.6)", letterSpacing: "1px" }}>
              STELLAR SOROBAN AGENT SDK
            </span>
          </div>

          {/* Typographic Headline */}
          <h1 style={{
            fontSize: "clamp(2.5rem, 6vw, 4.8rem)",
            fontWeight: 800,
            lineHeight: "1.1",
            letterSpacing: "-0.04em",
            color: "#ffffff",
            marginBottom: "24px",
            fontFamily: "var(--font-display)"
          }}>
            Every Agent Needs a Wallet.
          </h1>

          {/* Subheadline */}
          <h2 style={{
            fontSize: "clamp(1.1rem, 2.2vw, 1.4rem)",
            color: "rgba(255, 255, 255, 0.9)",
            lineHeight: "1.5",
            maxWidth: "720px",
            margin: "0 auto 40px auto",
            fontWeight: 500,
            fontFamily: "var(--font-sans)"
          }}>
            The Python-first framework for creating agents that discover, coordinate, and transact on Stellar.
          </h2>

          {/* Actions */}
          <div style={{
            display: "flex",
            flexDirection: "row",
            alignItems: "center",
            justifyContent: "center",
            gap: "16px",
            marginBottom: "80px"
          }}>
            <Link href="/playground" className="premium-button-primary">
              Launch Playground
              <ChevronRight size={16} />
            </Link>
            <a href="https://github.com" target="_blank" rel="noopener noreferrer" className="premium-button-secondary">
              Read SDK Docs
            </a>
          </div>
        </div>

        {/* Embedded Dark Terminal Window for high-contrast visual break */}
        <div style={{
          width: "100%",
          padding: "0 12px",
          position: "relative",
          zIndex: 20
        }}>
          <InteractiveTerminal />
        </div>
      </section>


      {/* Agent Architecture Section */}
      <section id="architecture" style={{
        position: "relative",
        zIndex: 10,
        padding: "120px 24px"
      }}>
        <div style={{
          maxWidth: "1200px",
          margin: "0 auto"
        }}>
          <div style={{
            textAlign: "center",
            marginBottom: "64px"
          }}>
            <span style={{
              fontSize: "0.75rem",
              fontFamily: "var(--font-mono)",
              color: "var(--accent-purple)",
              textTransform: "uppercase",
              letterSpacing: "2px",
              fontWeight: "bold",
              display: "block",
              marginBottom: "12px"
            }}>
              AGENT STACK
            </span>
            <h2 className="font-display" style={{
              fontSize: "clamp(2rem, 5vw, 2.8rem)",
              fontWeight: 800,
              color: "#ffffff",
              letterSpacing: "-0.03em"
            }}>
              One agent is useful.
              <br />
              <span className="font-serif" style={{ fontStyle: "italic", fontWeight: "normal" }}>A network of agents</span> changes everything.
            </h2>
            <p style={{
              fontSize: "1.05rem",
              color: "rgba(255, 255, 255, 0.6)",
              maxWidth: "600px",
              margin: "16px auto 0 auto",
              fontWeight: 300,
              lineHeight: "1.6"
            }}>
              Together they form autonomous economic systems.
            </p>
          </div>

          <AgentArchitecture />
        </div>
      </section>

      {/* Features Grid Section */}
      <section id="features" style={{
        position: "relative",
        zIndex: 10,
        padding: "120px 24px",
        borderTop: "1px solid rgba(255, 255, 255, 0.06)"
      }}>
        <div style={{
          maxWidth: "1200px",
          margin: "0 auto"
        }}>
          <div style={{
            textAlign: "center",
            marginBottom: "72px"
          }}>
            <span style={{
              fontSize: "0.75rem",
              fontFamily: "var(--font-mono)",
              color: "var(--accent-cyan)",
              textTransform: "uppercase",
              letterSpacing: "2px",
              fontWeight: "bold",
              display: "block",
              marginBottom: "12px"
            }}>
              FEATURES
            </span>
            <h2 className="font-display" style={{
              fontSize: "clamp(1.8rem, 4vw, 2.3rem)",
              fontWeight: 700,
              color: "#ffffff"
            }}>
              A Complete Toolkit for Autonomous Agents
            </h2>
            <p style={{
              fontSize: "0.95rem",
              color: "rgba(255, 255, 255, 0.5)",
              maxWidth: "500px",
              margin: "12px auto 0 auto",
              fontWeight: 300,
              lineHeight: "1.6"
            }}>
              Everything you need to build, test, and deploy smart contract pipelines on the Stellar Soroban network.
            </p>
          </div>

          {/* Grid Layout */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
            gap: "24px"
          }}>
            {features.map((feat, idx) => (
              <div
                key={idx}
                className="premium-card"
                style={{
                  padding: "32px",
                  borderRadius: "12px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "16px",
                  height: "100%"
                }}
              >
                <div style={{
                  color: idx % 2 === 0 ? "var(--accent-cyan)" : "var(--accent-purple)",
                  width: "fit-content",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center"
                }}>
                  {feat.icon}
                </div>
                <div>
                  <h3 className="font-display" style={{
                    fontSize: "1.25rem",
                    fontWeight: 700,
                    color: "#ffffff"
                  }}>
                    {feat.title}
                  </h3>
                  {feat.subtitle && (
                    <div style={{
                      fontSize: "0.75rem",
                      fontFamily: "var(--font-mono)",
                      color: idx % 2 === 0 ? "var(--accent-cyan)" : "var(--accent-purple)",
                      textTransform: "uppercase",
                      letterSpacing: "0.5px",
                      marginTop: "4px"
                    }}>
                      {feat.subtitle}
                    </div>
                  )}
                </div>
                <p style={{
                  fontSize: "0.85rem",
                  color: "rgba(255, 255, 255, 0.6)",
                  lineHeight: "1.6",
                  fontWeight: 300
                }}>
                  {feat.desc}
                </p>
                {feat.list && (
                  <ul style={{ listStyle: "none", padding: 0, margin: "8px 0 0 0", display: "flex", flexDirection: "column", gap: "6px" }}>
                    {feat.list.map((item, lIdx) => (
                      <li key={lIdx} style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                        fontSize: "0.75rem",
                        color: "rgba(255, 255, 255, 0.5)",
                        fontFamily: feat.isCli ? "var(--font-mono)" : "var(--font-sans)"
                      }}>
                        {feat.isCli ? (
                          <span style={{ color: "var(--accent-cyan)", fontWeight: "bold" }}>$</span>
                        ) : (
                          <span style={{
                            width: "4px",
                            height: "4px",
                            borderRadius: "50%",
                            backgroundColor: idx % 2 === 0 ? "var(--accent-cyan)" : "var(--accent-purple)"
                          }} />
                        )}
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {feat.buttonText && (
                  <div style={{ marginTop: "auto", paddingTop: "12px" }}>
                    <Link href={feat.buttonLink} style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "4px",
                      fontSize: "0.8rem",
                      fontWeight: 600,
                      color: idx % 2 === 0 ? "var(--accent-cyan)" : "var(--accent-purple)"
                    }}>
                      {feat.buttonText} <ArrowRight size={12} />
                    </Link>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Vision Section */}
      <section style={{
        position: "relative",
        zIndex: 10,
        padding: "140px 24px 140px 24px",
        textAlign: "center",
        borderTop: "1px solid rgba(255, 255, 255, 0.06)",
        background: "rgba(255, 255, 255, 0.01)"
      }}>
        <div style={{ maxWidth: "800px", margin: "0 auto" }}>
          <span style={{
            fontSize: "0.75rem",
            fontFamily: "var(--font-mono)",
            color: "var(--accent-cyan)",
            textTransform: "uppercase",
            letterSpacing: "2px",
            fontWeight: "bold",
            display: "block",
            marginBottom: "20px"
          }}>
            VISION STATEMENT
          </span>
          <h2 className="font-display" style={{
            fontSize: "clamp(2rem, 5vw, 3.4rem)",
            fontWeight: 800,
            lineHeight: "1.2",
            marginBottom: "32px",
            letterSpacing: "-0.04em",
            color: "#ffffff"
          }}>
            A New Economic Species Is Emerging.
          </h2>
          <div style={{
            fontSize: "clamp(1.1rem, 2.5vw, 1.45rem)",
            color: "rgba(255, 255, 255, 0.85)",
            lineHeight: "1.7",
            marginBottom: "40px",
            fontWeight: 300,
            fontFamily: "var(--font-serif)",
            fontStyle: "italic"
          }}>
            “For centuries, software executed instructions.
            <br />
            Now software can discover, coordinate, and transact.”
          </div>
          <p style={{
            fontSize: "clamp(0.95rem, 2vw, 1.15rem)",
            color: "rgba(255, 255, 255, 0.55)",
            lineHeight: "1.6",
            maxWidth: "600px",
            margin: "0 auto 48px auto",
            fontWeight: 300
          }}>
            The next economy will not be built solely by humans. It will be built by autonomous agents.
          </p>
          <div style={{
            display: "flex",
            justifyContent: "center",
            gap: "16px"
          }}>
            <Link href="/playground" className="premium-button-primary">
              Launch Playground
              <ChevronRight size={16} />
            </Link>
            <a href="https://github.com" target="_blank" rel="noopener noreferrer" className="premium-button-secondary">
              Read SDK Docs
            </a>
          </div>
        </div>
      </section>

      {/* Sleek Premium Footer */}
      <footer style={{
        position: "relative",
        zIndex: 10,
        background: "rgba(255, 255, 255, 0.01)",
        borderTop: "1px solid rgba(255, 255, 255, 0.06)",
        padding: "48px 24px"
      }}>
        <div style={{
          maxWidth: "1200px",
          margin: "0 auto",
          display: "flex",
          flexDirection: "column",
          gap: "32px"
        }}>
          {/* Logo and tag */}
          <div style={{
            display: "flex",
            flexDirection: "row",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "16px"
          }}>
            <div>
              <span className="font-display" style={{
                fontSize: "1rem",
                fontWeight: 800,
                letterSpacing: "-0.03em"
              }}>
                Mycelium
              </span>
              <p style={{
                fontSize: "0.75rem",
                color: "rgba(255, 255, 255, 0.4)",
                marginTop: "6px",
                fontWeight: 300
              }}>
                Building the Infrastructure for Autonomous Economies.
              </p>
            </div>
            <div style={{
              fontSize: "0.75rem",
              fontFamily: "var(--font-mono)",
              color: "rgba(255, 255, 255, 0.4)",
              display: "flex",
              alignItems: "center",
              gap: "8px"
            }}>
              <span>v0.1.0-alpha</span>
              <span>•</span>
              <span>Powered by Stellar Soroban</span>
            </div>
          </div>

          <hr style={{ border: "none", borderTop: "1px solid rgba(255, 255, 255, 0.06)" }} />

          {/* Links and copyright */}
          <div style={{
            display: "flex",
            flexDirection: "row",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "16px",
            fontSize: "0.75rem",
            color: "rgba(255, 255, 255, 0.4)",
            fontWeight: 300
          }}>
            <span>© 2026 Mycelium. All rights reserved.</span>
            <div style={{ display: "flex", gap: "20px" }}>
              <a href="https://stellar.org" target="_blank" rel="noopener noreferrer" style={{ color: "rgba(255, 255, 255, 0.4)", textShadow: "none" }} onMouseEnter={(e) => e.currentTarget.style.color = "#ffffff"} onMouseLeave={(e) => e.currentTarget.style.color = "rgba(255, 255, 255, 0.4)"}>Stellar Network</a>
              <a href="https://github.com" target="_blank" rel="noopener noreferrer" style={{ color: "rgba(255, 255, 255, 0.4)", textShadow: "none" }} onMouseEnter={(e) => e.currentTarget.style.color = "#ffffff"} onMouseLeave={(e) => e.currentTarget.style.color = "rgba(255, 255, 255, 0.4)"}>GitHub</a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
