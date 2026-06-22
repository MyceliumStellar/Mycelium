"use client";

import React, { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Terminal, Code, Cpu, ShoppingBag, Layers,
  Search, Menu, X, Zap, Globe, Package,
  Play, ExternalLink, Network
} from "lucide-react";

// Navigation definition matching original page sections
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

type NavItem = { id: string; label: string; parentId?: string };

// Helper to normalize the pathname into our standard IDs
function getActiveSectionFromPath(pathname: string): { activeParent: string; activeChild: string } {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length < 2) {
    return { activeParent: "introduction", activeChild: "" };
  }
  
  const rawSlug = decodeURIComponent(parts[1]);
  // Normalise spaces, casing, underscores
  const s = rawSlug.toLowerCase().replace(/[\s_-]+/g, "-");
  
  let activeParent = "introduction";
  let activeChild = "";

  // Check if it matches any parent ID or children
  for (const item of NAV) {
    // Normalise parent id
    const parentNorm = item.id.toLowerCase().replace(/[\s_-]+/g, "-");
    const rawSlugNorm = s;

    // Check mapping variations
    const isParentMatch = 
      parentNorm === rawSlugNorm ||
      (parentNorm === "build-agent" && (rawSlugNorm === "build-your-first-agent" || rawSlugNorm === "build-agent")) ||
      (parentNorm === "deploy" && (rawSlugNorm === "deploy-to-stellar" || rawSlugNorm === "deploy")) ||
      (parentNorm === "sdk" && (rawSlugNorm === "sdk-reference" || rawSlugNorm === "sdk")) ||
      (parentNorm === "cli" && (rawSlugNorm === "cli-reference" || rawSlugNorm === "cli"));

    if (isParentMatch) {
      activeParent = item.id;
      break;
    }
  }

  return { activeParent, activeChild };
}

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeParent, setActiveParent] = useState("introduction");
  const [activeChild, setActiveChild] = useState("");

  const searchInputRef = useRef<HTMLInputElement | null>(null);

  // Global Ctrl+K / Cmd+K listener to focus search input
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Update active navigation keys when route changes
  useEffect(() => {
    const { activeParent: parent, activeChild: child } = getActiveSectionFromPath(pathname);
    setActiveParent(parent);
    setActiveChild(child);
  }, [pathname]);

  // Handle active child tracking from window hash URL updates
  useEffect(() => {
    const handleHashChange = () => {
      const hash = window.location.hash.replace("#", "");
      if (hash) {
        setActiveChild(hash);
      }
    };
    
    handleHashChange();
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  const handleNav = (id: string, isChild = false, parentId?: string) => {
    setSidebarOpen(false);
    if (isChild && parentId) {
      // Navigate to /docs/Parent#Child
      router.push(`/docs/${parentId}#${id}`);
      setActiveChild(id);
    } else {
      // Navigate to /docs/Parent
      router.push(`/docs/${id}`);
      setActiveChild("");
    }
  };

  // Build flattened list of links for search index
  const flatItems: NavItem[] = [];
  for (const item of NAV) {
    flatItems.push({ id: item.id, label: item.label });
    if ("children" in item) {
      for (const c of item.children) {
        flatItems.push({ id: c.id, label: c.label, parentId: item.id });
      }
    }
  }

  const filtered = searchQuery
    ? flatItems.filter(i => i.label.toLowerCase().includes(searchQuery.toLowerCase()))
    : null;

  return (
    <div style={{
      background: "var(--background)",
      color: "var(--foreground)",
      minHeight: "100vh",
      fontFamily: "var(--font-sans)",
    }}>
      {/* ── Top header bar ── */}
      <header style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: 150,
        background: "rgba(4, 4, 5, 0.9)",
        backdropFilter: "blur(16px)",
        WebkitBackdropFilter: "blur(16px)",
        borderBottom: "1px solid rgba(255, 255, 255, 0.06)",
        height: "64px"
      }}>
        <div style={{
          width: "100%",
          height: "100%",
          padding: "0 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between"
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            {/* Hamburger */}
            <button
              className="docs-hamburger"
              onClick={() => setSidebarOpen(v => !v)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: 36,
                height: 36,
                background: "rgba(255, 255, 255, 0.03)",
                border: "1px solid rgba(255, 255, 255, 0.08)",
                borderRadius: "6px",
                cursor: "pointer",
                color: "rgba(255, 255, 255, 0.6)",
              }}
            >
              {sidebarOpen ? <X size={18} /> : <Menu size={18} />}
            </button>

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
          </div>

          <nav style={{ display: "none", gap: "28px" }} className="md-nav-links">
            <a href="/#features"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >features</a>
            <a href="/#architecture"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >architecture</a>
            <Link href="/agent"
              style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)", transition: "color 0.2s" }}
              onMouseEnter={e => e.currentTarget.style.color = "#fff"}
              onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.45)"}
            >agents</Link>
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
            fontSize: "0.76rem",
            borderRadius: "6px"
          }}>
            Launch Playground
          </Link>
        </div>
      </header>

      {/* ── Left Sidebar ── */}
      <>
        {sidebarOpen && (
          <div
            onClick={() => setSidebarOpen(false)}
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
        }} className={`docs-sidebar${sidebarOpen ? " docs-sidebar-open" : ""}`}>
          {/* Brand */}
          <div style={{
            padding: "18px 20px 14px",
            borderBottom: "1px solid rgba(255,255,255,0.06)",
            display: "flex", alignItems: "center", justifyContent: "space-between",
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
                ref={searchInputRef}
                placeholder="Search docs..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                style={{
                  flex: 1, minWidth: 0, background: "none", border: "none", outline: "none",
                  color: "#fff", fontSize: "0.82rem", fontFamily: "var(--font-sans)",
                }}
              />
              {!searchQuery && (
                <span style={{
                  flexShrink: 0,
                  fontSize: "0.6rem",
                  padding: "2px 5px",
                  borderRadius: 4,
                  background: "rgba(255, 255, 255, 0.08)",
                  color: "rgba(255, 255, 255, 0.35)",
                  border: "1px solid rgba(255, 255, 255, 0.08)",
                  fontFamily: "var(--font-mono)",
                  userSelect: "none",
                  pointerEvents: "none",
                }}>
                  Ctrl+K
                </span>
              )}
              {searchQuery && (
                <button onClick={() => setSearchQuery("")} style={{ background: "none", border: "none", cursor: "pointer", color: "rgba(255,255,255,0.3)", padding: 0 }}>
                  <X size={11} />
                </button>
              )}
            </div>
          </div>

          {/* Nav Links */}
          <nav style={{ flex: 1, padding: "10px 10px 24px", overflowY: "auto" }}>
            {filtered ? (
              <div>
                <p style={{ fontSize: "0.7rem", color: "rgba(255,255,255,0.3)", padding: "4px 10px 8px", fontFamily: "var(--font-sans)" }}>
                  {filtered.length} results
                </p>
                {filtered.map(item => {
                  const isChild = !!item.parentId;
                  const active = isChild ? activeChild === item.id : activeParent === item.id;
                  return (
                    <button
                      key={item.id}
                      onClick={() => handleNav(item.id, isChild, item.parentId)}
                      style={{
                        display: "flex", alignItems: "center", gap: 7,
                        width: "100%", textAlign: "left",
                        padding: isChild ? "4px 10px 4px 26px" : "5px 10px",
                        borderRadius: 5, border: "none", cursor: "pointer",
                        background: active ? "rgba(0,150,199,0.08)" : "transparent",
                        color: active ? "var(--accent-cyan)" : isChild ? "rgba(255,255,255,0.45)" : "rgba(255,255,255,0.7)",
                        fontSize: isChild ? "0.79rem" : "0.83rem",
                        fontWeight: active ? 600 : isChild ? 400 : 500,
                        fontFamily: "var(--font-sans)",
                        transition: "all 0.15s",
                        borderLeft: active ? "2px solid var(--accent-cyan)" : "2px solid transparent",
                        marginBottom: isChild ? 0 : 1,
                      }}
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
            ) : (
              NAV.map(item => {
                const active = activeParent === item.id;
                return (
                  <div key={item.id}>
                    <button
                      onClick={() => handleNav(item.id, false)}
                      style={{
                        display: "flex", alignItems: "center", gap: 7,
                        width: "100%", textAlign: "left",
                        padding: "5px 10px",
                        borderRadius: 5, border: "none", cursor: "pointer",
                        background: active ? "rgba(0,150,199,0.08)" : "transparent",
                        color: active ? "var(--accent-cyan)" : "rgba(255,255,255,0.7)",
                        fontSize: "0.83rem",
                        fontWeight: active ? 600 : 500,
                        fontFamily: "var(--font-sans)",
                        transition: "all 0.15s",
                        borderLeft: active ? "2px solid var(--accent-cyan)" : "2px solid transparent",
                        marginBottom: 1,
                      }}
                    >
                      {"icon" in item && <span style={{ opacity: 0.6 }}>{item.icon}</span>}
                      {item.label}
                    </button>
                    {/* Render children if this node is active OR if the children are active */}
                    {"children" in item && (active || item.children.some(c => activeChild === c.id)) && (
                      <div style={{ marginTop: 2, marginBottom: 4 }}>
                        {item.children.map(child => {
                          const childActive = activeChild === child.id;
                          return (
                            <button
                              key={child.id}
                              onClick={() => handleNav(child.id, true, item.id)}
                              style={{
                                display: "flex", alignItems: "center", gap: 7,
                                width: "100%", textAlign: "left",
                                padding: "4px 10px 4px 26px",
                                borderRadius: 5, border: "none", cursor: "pointer",
                                background: childActive ? "rgba(0,150,199,0.04)" : "transparent",
                                color: childActive ? "var(--accent-cyan)" : "rgba(255,255,255,0.45)",
                                fontSize: "0.79rem",
                                fontWeight: childActive ? 600 : 400,
                                fontFamily: "var(--font-sans)",
                                transition: "all 0.15s",
                                borderLeft: childActive ? "2px solid var(--accent-cyan)" : "2px solid transparent",
                                marginBottom: 1,
                              }}
                            >
                              {child.label}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </nav>

          {/* Footer links */}
          <div style={{
            padding: "14px 18px",
            borderTop: "1px solid rgba(255,255,255,0.06)",
            display: "flex", flexDirection: "column", gap: 6,
          }}>
            <a href="https://github.com/Srizdebnath" target="_blank" rel="noreferrer" style={{
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

      {/* ── Main Layout Container ── */}
      <main className="docs-main" style={{ minHeight: "100vh", paddingTop: "64px" }}>
        {children}
      </main>
    </div>
  );
}
