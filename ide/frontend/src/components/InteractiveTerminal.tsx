"use client";

import React, { useState, useEffect, useRef } from "react";
import { Terminal, Copy, Check, RotateCcw } from "lucide-react";

interface TerminalLine {
  type: "command" | "output" | "success";
  text: string;
}

export default function InteractiveTerminal() {
  const [lines, setLines] = useState<TerminalLine[]>([]);
  const [currentInput, setCurrentInput] = useState("");
  const [commandIndex, setCommandIndex] = useState(0);
  const [isTyping, setIsTyping] = useState(false);
  const [copied, setCopied] = useState(false);
  const [hasFinished, setHasFinished] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);

  const commands = [
    {
      cmd: "pip install mycelium-stellar",
      outputs: [
        "Downloading mycelium_stellar-0.5.1-py3-none-any.whl (64 kB)",
        "Installing collected packages: mycelium-stellar, mycelium-sdk, mycelium-cli, mycelium-compiler",
        "Successfully installed mycelium-stellar-0.5.1"
      ]
    },
    {
      cmd: "mycelium init my_project --non-interactive",
      outputs: [
        "✓ Project 'my_project' initialized.",
        "  Next: cd into it, run 'mycelium newwallet', then 'mycelium compile'."
      ]
    },
    {
      cmd: "mycelium newwallet",
      outputs: [
        "Encryption passphrase: *****",
        "✓ Wallet created at .mycelium/wallet.json",
        "  Public key: GBXAG4ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC"
      ]
    },
    {
      cmd: "mycelium compile",
      outputs: [
        "Compiling contract contract.py -> build/contract.wasm...",
        "✓ Compilation successful! Output: build/contract.wasm",
        "  WASM size: 14,352 bytes (14.02 KiB)"
      ]
    },
    {
      cmd: "mycelium deploy",
      outputs: [
        "[deploy] Deploying build/contract.wasm to testnet as GBXAG4ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC...",
        "[deploy] Wallet has 0 XLM — requesting Friendbot funding for GBXAG4ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC...",
        "[deploy] Friendbot funding confirmed on-chain.",
        "✓ Deployment successful! Contract ID: CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC",
        "  Wrote contract_id + wallet_public_key to mycelium.toml"
      ]
    }
  ];

  useEffect(() => {
    runScript();
  }, []);

  const runScript = async () => {
    setLines([]);
    setHasFinished(false);
    
    // Step through each command
    for (let i = 0; i < commands.length; i++) {
      setCommandIndex(i);
      setIsTyping(true);
      
      const fullCmd = commands[i].cmd;
      let typed = "";
      
      // Typing effect for the command
      for (let charIndex = 0; charIndex < fullCmd.length; charIndex++) {
        typed += fullCmd[charIndex];
        setCurrentInput(typed);
        await new Promise((r) => setTimeout(r, 50 + Math.random() * 30));
      }
      
      setIsTyping(false);
      setCurrentInput("");
      
      // Add the typed command to the lines list
      setLines((prev) => [...prev, { type: "command", text: `$ ${fullCmd}` }]);
      await new Promise((r) => setTimeout(r, 200));
      
      // Print the outputs line by line
      for (const outputLine of commands[i].outputs) {
        const isSuccess = outputLine.startsWith("✓");
        setLines((prev) => [
          ...prev,
          { 
            type: isSuccess ? "success" : "output", 
            text: outputLine 
          }
        ]);
        // Scroll to bottom
        if (containerRef.current) {
          containerRef.current.scrollTop = containerRef.current.scrollHeight;
        }
        await new Promise((r) => setTimeout(r, isSuccess ? 500 : 100));
      }
      
      await new Promise((r) => setTimeout(r, 600));
    }
    
    setHasFinished(true);
  };

  const copyToClipboard = () => {
    navigator.clipboard.writeText("pip install mycelium-stellar");
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={{
      width: "100%",
      maxWidth: "680px",
      margin: "0 auto",
      borderRadius: "10px",
      border: "1px solid rgba(255, 255, 255, 0.1)",
      background: "rgba(3, 3, 5, 0.75)",
      backdropFilter: "blur(12px)",
      WebkitBackdropFilter: "blur(12px)",
      overflow: "hidden",
      boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.5)"
    }}>
      {/* Terminal Header */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 18px",
        background: "rgba(255, 255, 255, 0.03)",
        borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
        userSelect: "none"
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <Terminal size={15} style={{ color: "rgba(255, 255, 255, 0.4)" }} />
          <span style={{
            fontSize: "0.7rem",
            fontFamily: "var(--font-mono)",
            color: "rgba(255, 255, 255, 0.4)",
            textTransform: "uppercase",
            letterSpacing: "2px",
            fontWeight: 600
          }}>
            mycelium -- shell
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          {hasFinished && (
            <button
              onClick={runScript}
              style={{
                background: "transparent",
                border: "none",
                padding: "4px",
                borderRadius: "4px",
                color: "rgba(255, 255, 255, 0.4)",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                transition: "all 0.2s"
              }}
              title="Re-run simulation"
            >
              <RotateCcw size={14} />
            </button>
          )}
          <button
            onClick={copyToClipboard}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "6px",
              padding: "4px 8px",
              borderRadius: "4px",
              border: "1px solid rgba(255, 255, 255, 0.08)",
              background: "rgba(255, 255, 255, 0.02)",
              fontSize: "0.75rem",
              color: "rgba(255, 255, 255, 0.5)",
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
              transition: "all 0.2s"
            }}
            title="Copy install command"
          >
            {copied ? (
              <>
                <Check size={12} style={{ color: "#00f2fe" }} />
                <span style={{ color: "#00f2fe" }}>Copied</span>
              </>
            ) : (
              <>
                <Copy size={12} />
                <span>pip install mycelium-stellar</span>
              </>
            )}
          </button>
          <div style={{ display: "flex", gap: "6px", marginLeft: "4px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255, 255, 255, 0.1)" }}></span>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255, 255, 255, 0.1)" }}></span>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "rgba(255, 255, 255, 0.1)" }}></span>
          </div>
        </div>
      </div>

      {/* Terminal Content */}
      <div 
        ref={containerRef}
        style={{
          padding: "20px",
          minHeight: "240px",
          maxHeight: "340px",
          overflowY: "auto",
          fontFamily: "var(--font-mono)",
          fontSize: "0.85rem",
          lineHeight: "1.6",
          textAlign: "left",
          scrollbarWidth: "none"
        }}
      >
        {/* Render lines */}
        {lines.map((line, idx) => (
          <div 
            key={idx} 
            style={{
              marginBottom: "6px",
              color: line.type === "command" 
                ? "#ffffff" 
                : line.type === "success"
                ? "var(--accent-cyan)"
                : "rgba(255, 255, 255, 0.6)",
              fontWeight: line.type === "command" || line.type === "success" ? "bold" : "normal"
            }}
          >
            {line.text}
          </div>
        ))}

        {/* Render typing line */}
        {isTyping && (
          <div style={{ color: "#ffffff", fontWeight: "bold" }}>
            $ {currentInput}
            <span style={{
              display: "inline-block",
              width: "6px",
              height: "15px",
              marginLeft: "4px",
              background: "rgba(255, 255, 255, 0.9)",
              animation: "pulse 1s infinite",
              verticalAlign: "middle"
            }}></span>
          </div>
        )}

        {/* Prompt when idle / finished */}
        {!isTyping && (
          <div style={{ color: "rgba(255, 255, 255, 0.25)" }}>
            $ <span style={{
              display: "inline-block",
              width: "6px",
              height: "15px",
              background: "rgba(255, 255, 255, 0.25)",
              animation: "pulse 1s infinite",
              verticalAlign: "middle"
            }}></span>
          </div>
        )}
      </div>
    </div>
  );
}
