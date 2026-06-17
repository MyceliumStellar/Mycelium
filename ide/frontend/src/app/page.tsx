import Link from "next/link";

export default function Home() {
  return (
    <div style={{
      position: "relative",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      width: "100vw",
      height: "100vh",
      background: "#000000",
      overflow: "hidden",
    }}>
      {/* Scanline aesthetic overlay */}
      <div className="scanlines"></div>

      {/* Retro background grid */}
      <div style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundImage: "linear-gradient(rgba(26, 26, 30, 0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(26, 26, 30, 0.4) 1px, transparent 1px)",
        backgroundSize: "40px 40px",
        pointerEvents: "none",
        zIndex: 1
      }}></div>

      {/* Main Branding Card */}
      <div className="panel-retro" style={{
        padding: "40px 60px",
        borderRadius: "0px",
        textAlign: "center",
        zIndex: 2,
        maxWidth: "680px",
        width: "90%",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        boxShadow: "0 0 40px rgba(0, 0, 0, 0.95), 0 0 2px var(--border-color)"
      }}>
        {/* Terminal Header Bar */}
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          width: "100%",
          borderBottom: "2px solid var(--border-color)",
          paddingBottom: "10px",
          marginBottom: "30px",
          fontFamily: "var(--font-mono)",
          fontSize: "0.8rem",
          color: "var(--text-muted)",
          textTransform: "uppercase",
          letterSpacing: "2px"
        }}>
          <span>System: Active</span>
          <div style={{ display: "flex", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--accent-red)" }}></span>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--accent-yellow)" }}></span>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "var(--accent-green)" }}></span>
          </div>
        </div>

        {/* Title */}
        <h1 className="crt-glow" style={{
          fontFamily: "var(--font-retro)",
          fontSize: "4.5rem",
          fontWeight: "normal",
          color: "var(--accent-cyan)",
          margin: "0 0 10px 0",
          letterSpacing: "4px",
          lineHeight: "1.1"
        }}>
          MYCELIUM
        </h1>

        {/* Subtitle */}
        <div style={{
          fontFamily: "var(--font-mono)",
          fontSize: "0.85rem",
          color: "var(--accent-green)",
          marginBottom: "30px",
          letterSpacing: "1px"
        }}>
          PYTHON-TO-SOROBAN SMART CONTRACT COMPILER & RUNTIME
        </div>

        {/* Tagline */}
        <p style={{
          fontFamily: "var(--font-retro)",
          color: "var(--foreground)",
          fontSize: "1.35rem",
          lineHeight: "1.4",
          marginBottom: "40px",
          maxWidth: "500px"
        }}>
          Write smart contracts in clean, strictly-typed Python. Compile directly to WebAssembly. Deploy to Stellar Soroban.
        </p>

        {/* CTA Button */}
        <Link href="/playground" className="btn-retro btn-retro-accent" style={{
          fontSize: "1.5rem",
          padding: "12px 36px",
          letterSpacing: "2px",
          display: "inline-block"
        }}>
          [ Go to Playground ]
        </Link>
      </div>

      {/* Footer Details */}
      <div style={{
        position: "absolute",
        bottom: "20px",
        fontFamily: "var(--font-mono)",
        fontSize: "0.7rem",
        color: "var(--text-muted)",
        zIndex: 2,
        letterSpacing: "1px"
      }}>
        v0.1.0-alpha • Powered by Stellar Soroban
      </div>
    </div>
  );
}
