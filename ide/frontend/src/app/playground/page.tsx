"use client";

import React, { useState, useEffect, useRef } from "react";
import MonacoEditor from "@monaco-editor/react";
import Link from "next/link";
import { 
  Folder, FileCode, Play, Cpu, Shield, Key, Plus, 
  Terminal as TermIcon, LogOut, Wallet, Check, AlertTriangle, ExternalLink, RefreshCw,
  Copy, Download, Cpu as CpuIcon, Database, Activity, Wifi, TerminalSquare, Zap,
  ArrowRight
} from "lucide-react";
import toast, { Toaster } from "react-hot-toast";
import { CONTRACT_TEMPLATES, TEMPLATE_CATEGORIES } from "../../data/contractTemplates";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface FileItem {
  name: string;
  sha: string;
}

interface RepoItem {
  name: string;
  full_name: string;
  default_branch: string;
}

export default function Playground() {
  // Authentication State
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [sessionToken, setSessionToken] = useState("");
  const [username, setUsername] = useState("");
  const [avatarUrl, setAvatarUrl] = useState("");
  const [githubTokenBypass, setGithubTokenBypass] = useState("");
  const [showTokenInput, setShowTokenInput] = useState(false);

  // Terminal prompt identity: "mycelium@<github-username>" once authenticated, else guest.
  const promptUser = (isAuthenticated && username ? username : "guest");
  const shellPrompt = `mycelium@${promptUser}:~$`;

  // Workspaces (GitHub Repos) & Files State
  const [workspaces, setWorkspaces] = useState<RepoItem[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState("");
  const [files, setFiles] = useState<FileItem[]>([]);
  const [activeFile, setActiveFile] = useState("");
  const [activeFileSha, setActiveFileSha] = useState("");
  const [editorContent, setEditorContent] = useState("");
  const [isCompiling, setIsCompiling] = useState(false);
  const [isDeploying, setIsDeploying] = useState(false);
  const [compiledWasm, setCompiledWasm] = useState("");

  // Contract Invocation & Draft States
  const [deployedContractId, setDeployedContractId] = useState("");
  // Agent-creation mode (opened from the /agent "Create Agent" wizard via
  // ?repo=<name>&mode=create): auto-loads the repo and shows a guided rail.
  const [creationMode, setCreationMode] = useState(false);
  const [isRegistering, setIsRegistering] = useState(false);
  const [registeredTx, setRegisteredTx] = useState("");
  const [selectedFunc, setSelectedFunc] = useState("");
  const [funcArgs, setFuncArgs] = useState<Record<string, string>>({});
  const [isInvoking, setIsInvoking] = useState(false);
  const [invocationLogs, setInvocationLogs] = useState<string[]>([]);
  const [hasDraft, setHasDraft] = useState(false);
  const [draftContent, setDraftContent] = useState("");

  // Wallet Connect State
  const [isWalletConnected, setIsWalletConnected] = useState(false);
  const [walletAddress, setWalletAddress] = useState("");
  const [walletType, setWalletType] = useState("");
  const [walletNetwork, setWalletNetwork] = useState("");
  const [showWalletModal, setShowWalletModal] = useState(false);
  const [walletBalance, setWalletBalance] = useState("N/A");

  // Terminal State & Active Tab
  const [activeTab, setActiveTab] = useState<"console" | "compiler" | "deploy" | "interaction" | "wallet" | "network" | "problems">("console");
  const [terminalLogs, setTerminalLogs] = useState<{ type: string; text: string; time: string }[]>([
    { type: "info", text: "Mycelium OS v0.2.0 loaded. Type 'help' for command list.", time: "23:27:18" },
    { type: "success", text: "Stellar Soroban Development Env: ONLINE", time: "23:27:18" }
  ]);
  const [compilerLogs, setCompilerLogs] = useState<{ type: string; text: string; time: string }[]>([]);
  const [deployLogs, setDeployLogs] = useState<{ type: string; text: string; time: string }[]>([]);
  const [networkPing, setNetworkPing] = useState<number | null>(null);
  const [isPinging, setIsPinging] = useState(false);

  // Interactive Terminal states
  const [terminalTheme, setTerminalTheme] = useState("cyan");
  const [cliInput, setCliInput] = useState("");
  const [cliHistory, setCliHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [isTerminalFullScreen, setIsTerminalFullScreen] = useState(false);
  const [problems, setProblems] = useState<{ line: number; message: string; type: "error" | "warning"; file: string }[]>([]);
  
  // Monaco Reference
  const editorRef = useRef<any>(null);

  // Drag Resizing States
  const [sidebarWidth, setSidebarWidth] = useState(250);
  const [terminalHeight, setTerminalHeight] = useState(280);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const [isResizingTerminal, setIsResizingTerminal] = useState(false);

  // Modal Inputs
  const [showNewWorkspaceModal, setShowNewWorkspaceModal] = useState(false);
  const [newWorkspaceName, setNewWorkspaceName] = useState("");
  const [showNewFileModal, setShowNewFileModal] = useState(false);

  // Contract Template Browser (benchmark contracts verified to compile to WASM)
  const [showTemplateBrowser, setShowTemplateBrowser] = useState(false);
  const [templateSearch, setTemplateSearch] = useState("");
  const [templateCategory, setTemplateCategory] = useState<string>("All");
  const [newFileName, setNewFileName] = useState("");

  const terminalEndRef = useRef<HTMLDivElement>(null);
  const hasCalledAuthRef = useRef(false);

  // 1. Check URL query params for GitHub OAuth callback code, or check localStorage for session
  useEffect(() => {
    // Check local storage session first
    const savedToken = localStorage.getItem("mycelium_jwt");
    const savedUser = localStorage.getItem("mycelium_username");
    const savedAvatar = localStorage.getItem("mycelium_avatar");

    if (savedToken && savedUser) {
      setSessionToken(savedToken);
      setUsername(savedUser);
      setAvatarUrl(savedAvatar || "");
      setIsAuthenticated(true);
      return;
    }

    // Process GitHub OAuth Callback code from URL query parameters
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    if (code && !hasCalledAuthRef.current) {
      hasCalledAuthRef.current = true;
      // Clean query parameter from URL bar to prevent refresh submissions
      window.history.replaceState({}, document.title, window.location.pathname);
      addTerminalLog("info", "GitHub authorization code detected. Authenticating callback...");
      exchangeOAuthCode(code);
    }
  }, []);

  // Fetch Workspaces on Load (if authenticated)
  useEffect(() => {
    if (isAuthenticated && sessionToken) {
      fetchWorkspaces();
      pingStellarNetwork();
    }
  }, [isAuthenticated, sessionToken]);

  // Agent-creation mode: when arriving from the wizard with ?repo=&mode=create,
  // open that repo and surface the Write → Compile → Deploy → Register rail.
  useEffect(() => {
    if (!isAuthenticated || !sessionToken) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("mode") === "create") {
      const repo = params.get("repo");
      if (repo) {
        setCreationMode(true);
        setActiveWorkspace(repo);
        addTerminalLog("success", `Agent-creation mode: loaded repo '${repo}'. Follow the guided steps: Write → Compile → Deploy → Register.`);
      }
    }
  }, [isAuthenticated, sessionToken]);

  // Fetch Files when active workspace changes
  useEffect(() => {
    if (activeWorkspace) {
      fetchFiles(activeWorkspace);
    } else {
      setFiles([]);
      setActiveFile("");
      setActiveFileSha("");
      setEditorContent("");
    }
  }, [activeWorkspace]);

  // Fetch File Content when active file changes
  useEffect(() => {
    if (activeWorkspace && activeFile) {
      fetchFileContent(activeWorkspace, activeFile);
    }
  }, [activeFile]);

  // Scroll to bottom of terminal
  useEffect(() => {
    if (terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [terminalLogs, compilerLogs, deployLogs, activeTab, problems]);

  // Sync function arguments when contract ID changes
  useEffect(() => {
    if (deployedContractId) {
      const methods = getContractMethods();
      if (methods.length > 0) {
        setSelectedFunc(methods[0].name);
        const initialArgs: Record<string, string> = {};
        methods[0].params.forEach(p => {
          initialArgs[p.name] = "";
        });
        setFuncArgs(initialArgs);
      } else {
        setSelectedFunc("");
        setFuncArgs({});
      }
    } else {
      setSelectedFunc("");
      setFuncArgs({});
    }
  }, [deployedContractId]);

  // Fetch real Stellar balance of the connected wallet via Horizon
  useEffect(() => {
    if (isWalletConnected && walletAddress) {
      setWalletBalance("Loading...");
      const isPublic = walletNetwork === "PUBLIC";
      const url = isPublic
        ? `https://horizon.stellar.org/accounts/${walletAddress}`
        : `https://horizon-testnet.stellar.org/accounts/${walletAddress}`;
      
      fetch(url)
        .then(res => {
          if (res.status === 404) {
            return { balances: [{ asset_type: "native", balance: "0.0000000" }] };
          }
          return res.json();
        })
        .then(data => {
          const native = data.balances?.find((b: any) => b.asset_type === "native");
          if (native) {
            const formatted = parseFloat(native.balance).toLocaleString("en-US", {
              minimumFractionDigits: 7,
              maximumFractionDigits: 7
            });
            setWalletBalance(`${formatted} XLM`);
          } else {
            setWalletBalance("0.0000000 XLM");
          }
        })
        .catch(() => {
          setWalletBalance("Error fetching balance");
        });
    } else {
      setWalletBalance("N/A");
    }
  }, [isWalletConnected, walletAddress, walletNetwork]);

  // OAuth Code Exchange
  const exchangeOAuthCode = async (code: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/auth/github/callback?code=${code}`, {
        method: "POST"
      });
      if (res.ok) {
        const data = await res.json();
        localStorage.setItem("mycelium_jwt", data.access_token);
        localStorage.setItem("mycelium_username", data.username);
        localStorage.setItem("mycelium_avatar", data.avatar_url);

        setSessionToken(data.access_token);
        setUsername(data.username);
        setAvatarUrl(data.avatar_url);
        setIsAuthenticated(true);

        addTerminalLog("success", `Successfully authenticated via GitHub OAuth! Logged in as ${data.username}`);
        
        // Clear query parameters in URL
        window.history.replaceState({}, document.title, window.location.pathname);
      } else {
        addTerminalLog("error", "Failed to exchange GitHub OAuth code for session.");
      }
    } catch (err) {
      addTerminalLog("error", "Error connecting to GitHub Auth backend.");
    }
  };

  const handleOAuthLoginRedirect = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/auth/github`);
      if (res.ok) {
        const data = await res.json();
        addTerminalLog("info", "Redirecting to GitHub OAuth portals...");
        window.location.href = data.url;
      } else {
        addTerminalLog("error", "Failed to construct GitHub OAuth authorization URL.");
      }
    } catch (err) {
      addTerminalLog("error", "Authentication gateway backend is currently offline.");
    }
  };

  const handleLogout = () => {
    localStorage.removeItem("mycelium_jwt");
    localStorage.removeItem("mycelium_username");
    localStorage.removeItem("mycelium_avatar");
    setSessionToken("");
    setUsername("");
    setAvatarUrl("");
    setIsAuthenticated(false);
    setActiveWorkspace("");
    setWorkspaces([]);
  };

  // API Call Helpers
  const apiFetch = async (url: string, options: RequestInit = {}) => {
    const headers = {
      ...(options.headers || {}),
      "Authorization": `Bearer ${sessionToken}`
    } as any;
    
    let res = await fetch(url, { ...options, headers });
    
    if (res.status === 401 && sessionToken) {
      try {
        const refreshRes = await fetch(`${API_BASE_URL}/auth/refresh`, {
          method: "POST",
          headers: { "Authorization": `Bearer ${sessionToken}` }
        });
        
        if (refreshRes.ok) {
          const refreshData = await refreshRes.json();
          const newToken = refreshData.access_token;
          localStorage.setItem("mycelium_jwt", newToken);
          setSessionToken(newToken);
          
          headers["Authorization"] = `Bearer ${newToken}`;
          res = await fetch(url, { ...options, headers });
        } else {
          handleLogout();
        }
      } catch (err) {
        handleLogout();
      }
    }
    return res;
  };

  const fetchWorkspaces = async () => {
    try {
      const res = await apiFetch(`${API_BASE_URL}/api/workspaces`);
      if (res.ok) {
        const data = await res.json();
        setWorkspaces(data);
        if (data.length > 0 && !activeWorkspace) {
          setActiveWorkspace(data[0].name);
        }
      } else {
        addTerminalLog("error", "Failed to retrieve your repository workspaces list.");
      }
    } catch (err) {
      addTerminalLog("error", "Connection error trying to fetch workspaces.");
    }
  };

  const fetchFiles = async (workspaceName: string) => {
    try {
      const res = await apiFetch(`${API_BASE_URL}/api/workspaces/${workspaceName}/files`);
      if (res.ok) {
        const data = await res.json();
        setFiles(data);
        if (data.length > 0) {
          setActiveFile(data[0].name);
          setActiveFileSha(data[0].sha);
        } else {
          setActiveFile("");
          setActiveFileSha("");
          setEditorContent("");
        }
      } else {
        addTerminalLog("error", `Branch in '${workspaceName}' is empty or uninitialized.`);
      }
    } catch (err) {
      addTerminalLog("error", `Failed to fetch repository files list.`);
    }
  };

  const fetchFileContent = async (workspaceName: string, filename: string) => {
    try {
      const res = await apiFetch(`${API_BASE_URL}/api/workspaces/${workspaceName}/files/${filename}`);
      if (res.ok) {
        const data = await res.json();
        const savedDraft = localStorage.getItem(`mycelium_draft_${workspaceName}_${filename}`);
        if (savedDraft && savedDraft !== data.content) {
          setHasDraft(true);
          setDraftContent(savedDraft);
          setEditorContent(data.content);
        } else {
          setHasDraft(false);
          setDraftContent("");
          setEditorContent(data.content);
        }
        setActiveFileSha(data.sha);
        setCompiledWasm("");
        setDeployedContractId("");
      }
    } catch (err) {
      addTerminalLog("error", `Failed to fetch file content: ${filename}`);
    }
  };

  const saveActiveFile = async () => {
    if (!activeWorkspace || !activeFile) return;
    try {
      addTerminalLog("info", `Committing changes for ${activeFile} directly to GitHub repository...`);
      const res = await apiFetch(`${API_BASE_URL}/api/workspaces/${activeWorkspace}/files`, {
        method: "POST",
        headers: { 
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ 
          filename: activeFile, 
          content: editorContent,
          sha: activeFileSha || null 
        })
      });
      if (res.ok) {
        const data = await res.json();
        setActiveFileSha(data.sha);
        addTerminalLog("success", `✓ File committed successfully to GitHub: ${activeFile} (sha: ${data.sha.slice(0, 7)})`);
        toast.success(`File '${activeFile}' committed successfully!`);
        fetchFiles(activeWorkspace);
        localStorage.removeItem(`mycelium_draft_${activeWorkspace}_${activeFile}`);
        setHasDraft(false);
        setDraftContent("");
      } else {
        const errData = await res.json();
        addTerminalLog("error", `Commit failed: ${errData.detail || "Conflict error"}`);
        toast.error(`Commit failed: ${errData.detail || "Conflict error"}`);
      }
    } catch (err) {
      addTerminalLog("error", "Network error while saving file.");
      toast.error("Network error while saving file.");
    }
  };

  const handleCreateWorkspace = async () => {
    if (!newWorkspaceName.trim()) return;
    try {
      addTerminalLog("info", `Requesting GitHub to create repository: ${newWorkspaceName}...`);
      const res = await apiFetch(`${API_BASE_URL}/api/workspaces`, {
        method: "POST",
        headers: { 
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ name: newWorkspaceName })
      });
      if (res.ok) {
        addTerminalLog("success", `✓ Repository '${newWorkspaceName}' successfully created on your GitHub account.`);
        setNewWorkspaceName("");
        setShowNewWorkspaceModal(false);
        fetchWorkspaces();
      } else {
        const errData = await res.json();
        addTerminalLog("error", errData.detail || "Failed to create repository.");
      }
    } catch (err) {
      addTerminalLog("error", "Error creating workspace repository.");
    }
  };

  const handleCreateFile = async () => {
    if (!newFileName.trim() || !activeWorkspace) return;
    let filename = newFileName.trim();
    if (!filename.endsWith(".py")) {
      filename += ".py";
    }
    try {
      addTerminalLog("info", `Scaffolding file '${filename}' to GitHub...`);
      const res = await apiFetch(`${API_BASE_URL}/api/workspaces/${activeWorkspace}/files`, {
        method: "POST",
        headers: { 
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ 
          filename, 
          content: "# New Smart Contract File\n",
          sha: null 
        })
      });
      if (res.ok) {
        const data = await res.json();
        addTerminalLog("success", `✓ File '${filename}' successfully added.`);
        setNewFileName("");
        setShowNewFileModal(false);
        fetchFiles(activeWorkspace);
        setActiveFile(filename);
        setActiveFileSha(data.sha);
      } else {
        const errData = await res.json();
        addTerminalLog("error", errData.detail || "Failed to commit new file.");
      }
    } catch (err) {
      addTerminalLog("error", "Error creating file.");
    }
  };

  const addTerminalLog = (type: string, text: string) => {
    const time = new Date().toLocaleTimeString();
    setTerminalLogs(prev => [...prev, { type, text, time }]);
  };

  const addCompilerLog = (type: string, text: string) => {
    const time = new Date().toLocaleTimeString();
    setCompilerLogs(prev => [...prev, { type, text, time }]);
  };

  const addDeployLog = (type: string, text: string) => {
    const time = new Date().toLocaleTimeString();
    setDeployLogs(prev => [...prev, { type, text, time }]);
  };

  const jumpToLine = (lineNumber: number) => {
    if (editorRef.current) {
      editorRef.current.revealLineInCenter(lineNumber);
      editorRef.current.setPosition({ lineNumber, column: 1 });
      editorRef.current.focus();
      addTerminalLog("info", `Jumping to line ${lineNumber} in editor.`);
    } else {
      addTerminalLog("error", `Editor not loaded yet. Unable to jump to line ${lineNumber}.`);
    }
  };

  const parseCompilerErrors = (logs: string) => {
    const lines = logs.split("\n");
    const result: { line: number; message: string; type: "error" | "warning"; file: string }[] = [];
    
    const pythonFileRegex = /File\s+"([^"]+)",\s+line\s+(\d+)/i;
    const errorTypeRegex = /^(TypeError|ValueError|SyntaxError|NameError|AttributeError|ValidationError):\s*(.*)$/i;
    const validationLineRegex = /(?:ValidationError|TypeError|ValueError):\s*Line\s*(\d+):\s*(.*)/i;
    const rustErrorLineRegex = /-->\s*src\/lib\.rs:(\d+)(?::(\d+))?/i;
    
    let currentLine: number | null = null;
    
    for (let i = 0; i < lines.length; i++) {
      const lineStr = lines[i].trim();
      
      const valMatch = lineStr.match(validationLineRegex);
      if (valMatch) {
        result.push({
          line: parseInt(valMatch[1], 10),
          message: valMatch[2],
          type: "error",
          file: activeFile || "contract.py"
        });
        continue;
      }
      
      const fileMatch = lineStr.match(pythonFileRegex);
      if (fileMatch) {
        const filePath = fileMatch[1];
        // Only map if it's not a compiler system file
        if (!filePath.includes("runpy") && !filePath.includes("main.py") && !filePath.includes("codegen.py") && !filePath.includes("parser.py")) {
          currentLine = parseInt(fileMatch[2], 10);
        }
        continue;
      }
      
      const errMatch = lineStr.match(errorTypeRegex);
      if (errMatch) {
        const errType = errMatch[1];
        const errMsg = errMatch[2];
        
        result.push({
          line: currentLine || 1,
          message: `${errType}: ${errMsg}`,
          type: "error",
          file: activeFile || "contract.py"
        });
        
        currentLine = null;
      }
    }
    
    if (result.length === 0) {
      for (let i = 0; i < lines.length; i++) {
        const lineStr = lines[i].trim();
        const rustMatch = lineStr.match(rustErrorLineRegex);
        if (rustMatch) {
          const rustLine = parseInt(rustMatch[1], 10);
          let message = "Rust Compilation Error";
          let type: "error" | "warning" = "error";
          for (let j = i - 1; j >= Math.max(0, i - 4); j--) {
            const prevLine = lines[j].trim();
            if (prevLine.startsWith("error:") || prevLine.startsWith("error[")) {
              message = prevLine;
              type = "error";
              break;
            } else if (prevLine.startsWith("warning:") || prevLine.startsWith("warning[")) {
              message = prevLine;
              type = "warning";
              break;
            }
          }
          result.push({
            line: rustLine,
            message: message,
            type: type,
            file: activeFile || "contract.py"
          });
        }
      }
    }
    
    if (result.length === 0) {
      const lineNumMatch = logs.match(/(?:line|Line)\s*(\d+)/);
      if (lineNumMatch) {
        const hasTraceback = logs.includes("Traceback (most recent call last)");
        const line = hasTraceback ? 1 : parseInt(lineNumMatch[1], 10);
        
        result.push({
          line: line,
          message: logs.split("\n").filter(l => l.includes("Error") || l.includes("Failed") || l.includes("unsupported")).join(" ") || "Compilation Failed",
          type: "error",
          file: activeFile || "contract.py"
        });
      }
    }
    
    return result;
  };

  const getThemeColors = () => {
    switch (terminalTheme) {
      case "green":
        return {
          bg: "#020502",
          text: "#0df20d",
          border: "#053005",
          accent: "#39ff14",
          caret: "#0df20d"
        };
      case "amber":
        return {
          bg: "#050300",
          text: "#ffb000",
          border: "#402000",
          accent: "#ffcc00",
          caret: "#ffb000"
        };
      case "matrix":
        return {
          bg: "#000000",
          text: "#00ff00",
          border: "#003300",
          accent: "#33ff33",
          caret: "#00ff00"
        };
      case "stealth":
        return {
          bg: "#020203",
          text: "#e0e0e6",
          border: "#1c1c20",
          accent: "#9090a0",
          caret: "#ffffff"
        };
      case "cyan":
      default:
        return {
          bg: "#010304",
          text: "#00f2fe",
          border: "#102530",
          accent: "#05ffc5",
          caret: "#00f2fe"
        };
    }
  };

  const executeCliCommand = async (commandString: string) => {
    const parts = commandString.split(/\s+/);
    const baseCmd = parts[0].toLowerCase();
    const args = parts.slice(1);
    
    switch (baseCmd) {
      case "help":
        addTerminalLog("info", "Available Mycelium OS CLI Commands:");
        addTerminalLog("info", "  help      - Display this command reference help screen.");
        addTerminalLog("info", "  compile   - Compile active Python file statelessly.");
        addTerminalLog("info", "  deploy    - Deploy compiled WASM target (deploy testnet/mainnet).");
        addTerminalLog("info", "  wallet    - Output active cryptographic Stellar wallet status.");
        addTerminalLog("info", "  network   - Run latency ping diagnostics against Horizon node.");
        addTerminalLog("info", "  networks  - Compare Stellar Network metrics (Mainnet vs Testnet vs Futurenet).");
        addTerminalLog("info", "  sysinfo   - Display system environment metadata & build versions.");
        addTerminalLog("info", "  file      - Output metrics of active contract editor buffer.");
        addTerminalLog("info", "  themes    - List or toggle terminal screen overlay visual themes.");
        addTerminalLog("info", "  clear     - Wipe all logs from current terminal console tab.");
        break;
        
      case "compile":
        addTerminalLog("info", "CLI: Triggering compiler sandbox execution...");
        await handleCompile();
        break;
        
      case "deploy":
        const targetNet = (args[0] && args[0].toLowerCase() === "mainnet") ? "mainnet" : "testnet";
        addTerminalLog("info", `CLI: Triggering ledger transaction deployment to ${targetNet === "mainnet" ? "Mainnet" : "Testnet"}...`);
        await handleDeploy(targetNet);
        break;

      case "stellar":
        if (args[0] === "contract" && args[1] === "deploy") {
          addTerminalLog("info", "CLI: Triggering stellar contract deploy to Testnet...");
          await handleCompile();
          await handleDeploy("testnet");
        } else if (args[0] === "contract" && args[1] === "invoke") {
          const idIndex = args.indexOf("--id");
          let targetId = deployedContractId;
          if (idIndex !== -1 && args[idIndex + 1]) {
            targetId = args[idIndex + 1];
          }
          
          const doubleDashIndex = args.indexOf("--");
          if (doubleDashIndex === -1) {
            addTerminalLog("error", "Error: Missing '--' separator before function name.");
            break;
          }
          
          const fnAndArgs = args.slice(doubleDashIndex + 1);
          if (fnAndArgs.length === 0) {
            addTerminalLog("error", "Error: Missing function name after '--'.");
            break;
          }
          
          const fnName = fnAndArgs[0];
          const cmdArgs = fnAndArgs.slice(1);
          
          const argsMap: Record<string, string> = {};
          for (let i = 0; i < cmdArgs.length; i++) {
            if (cmdArgs[i].startsWith("--")) {
              const name = cmdArgs[i].replace(/^--/, "");
              const val = cmdArgs[i+1];
              if (val && !val.startsWith("--")) {
                argsMap[name] = val;
                i++;
              } else {
                argsMap[name] = "true";
              }
            }
          }
          
          if (!targetId) {
            addTerminalLog("error", "Error: No deployed contract ID found. Deploy first or specify --id <CONTRACT_ID>.");
            break;
          }
          
          addTerminalLog("info", `CLI: Invoking function [${fnName}] on contract ${targetId}...`);
          setSelectedFunc(fnName);
          setFuncArgs(argsMap);
          setDeployedContractId(targetId);
          setActiveTab("deploy");
          setTimeout(() => {
            handleInvokeCall(fnName, argsMap, targetId);
          }, 100);
        } else {
          addTerminalLog("error", "Error: Unknown stellar subcommand. Usage: 'stellar contract deploy' or 'stellar contract invoke --id <id> -- <func>'");
        }
        break;
        
      case "wallet":
        addTerminalLog("info", "Stellar Wallet Status Report:");
        addTerminalLog("info", `  Connected: ${isWalletConnected ? "YES" : "NO"}`);
        addTerminalLog("info", `  Address:   ${walletAddress || "N/A"}`);
        addTerminalLog("info", `  Type:      ${walletType || "N/A"}`);
        addTerminalLog("info", `  Network:   ${walletNetwork || "N/A"}`);
        break;
        
      case "network":
        addTerminalLog("info", "CLI: Initiating network ping diagnostic...");
        await pingStellarNetwork();
        break;

      case "networks":
        addTerminalLog("info", "Stellar Network Metrics Comparison Reference:");
        addTerminalLog("info", "+--------------------+-----------------------------+-----------------------------+-----------------------------+");
        addTerminalLog("info", "| Feature            | Mainnet                     | Testnet                     | Futurenet                   |");
        addTerminalLog("info", "+--------------------+-----------------------------+-----------------------------+-----------------------------+");
        addTerminalLog("info", "| Purpose            | Production network          | Stable testing environment  | Bleeding-edge features      |");
        addTerminalLog("info", "| Network Passphrase | Public Global Stellar...    | Test SDF Network...         | Test SDF Future Network...  |");
        addTerminalLog("info", "| Horizon API        | Multiple providers          | https://horizon-testnet...  | https://horizon-future...   |");
        addTerminalLog("info", "| Stellar RPC        | Third-party providers only  | https://soroban-testnet...  | https://rpc-futurenet...    |");
        addTerminalLog("info", "| Funding            | Real XLM required           | Free via Friendbot          | Free via Friendbot          |");
        addTerminalLog("info", "| Validator Nodes    | Run by the public           | 3 core validator nodes      | Core validator nodes        |");
        addTerminalLog("info", "| Friendbot          | No                          | Yes (10,000 XLM)            | Yes (10,000 XLM)            |");
        addTerminalLog("info", "| Network Resets     | Never                       | Regular cadence             | As needed (unpredictable)   |");
        addTerminalLog("info", "| Op/Ledger limit    | 1,000                       | 100                         | 100                         |");
        addTerminalLog("info", "| SmartContract/Ledg | Max 100*                    | 1                           | 1                           |");
        addTerminalLog("info", "+--------------------+-----------------------------+-----------------------------+-----------------------------+");
        break;
        
      case "sysinfo":
        addTerminalLog("info", "Mycelium Developer System Info:");
        addTerminalLog("info", "  OS Version:     Linux x86_64");
        addTerminalLog("info", "  Mycelium core:  v0.2.0");
        addTerminalLog("info", "  Next.js Build:  v16.2.9");
        addTerminalLog("info", "  React Fiber:    v19.0.0");
        addTerminalLog("info", `  Backend API:    FastAPI on ${API_BASE_URL}`);
        addTerminalLog("info", `  Active User:    ${username || "Anonymous"}`);
        break;
        
      case "file":
        if (!activeFile) {
          addTerminalLog("error", "No active file loaded in the editor workspace.");
        } else {
          addTerminalLog("info", `File Profile: ${activeFile}`);
          addTerminalLog("info", `  SHA hash:   ${activeFileSha || "Not saved yet"}`);
          addTerminalLog("info", `  Size:       ${editorContent.length} bytes`);
          addTerminalLog("info", `  Lines:      ${editorContent.split("\n").length} lines`);
        }
        break;
        
      case "themes":
        if (args.length === 0) {
          addTerminalLog("info", "Terminal Screen Themes: use 'themes <name>'");
          addTerminalLog("info", "  green   - Classic Green CRT Phosphor");
          addTerminalLog("info", "  amber   - Fallout Amber Pip-Boy");
          addTerminalLog("info", "  cyan    - Cyberpunk Neon Turquoise");
          addTerminalLog("info", "  matrix  - Code Matrix Falling Rain Green");
          addTerminalLog("info", "  stealth - Sleek Pitch-Black Obsidian");
          addTerminalLog("info", `Current Theme: ${terminalTheme}`);
        } else {
          const newTheme = args[0].toLowerCase();
          if (["green", "amber", "cyan", "matrix", "stealth"].includes(newTheme)) {
            setTerminalTheme(newTheme);
            addTerminalLog("success", `✓ Visual theme updated to: ${newTheme}`);
          } else {
            addTerminalLog("error", `Unknown theme: '${newTheme}'. Type 'themes' for list.`);
          }
        }
        break;
        
      case "clear":
        setTerminalLogs([]);
        break;
        
      default:
        addTerminalLog("error", `Command not found: '${baseCmd}'. Type 'help' for command manual.`);
        break;
    }
  };

  const handleCliSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const cmd = cliInput.trim();
    if (!cmd) return;
    
    addTerminalLog("stdout", `${shellPrompt} ${cmd}`);
    setCliHistory(prev => [cmd, ...prev]);
    setHistoryIndex(-1);
    setCliInput("");
    
    executeCliCommand(cmd);
  };

  const injectTemplate = (tpl: { label: string; source: string }) => {
    setEditorContent(tpl.source);
    setShowTemplateBrowser(false);
    addTerminalLog("success", `Loaded "${tpl.label}" template into the editor.`);
    addTerminalLog("info", "This contract is verified to compile to Soroban WASM. Hit Compile to build it.");
  };

  const handleCompile = async () => {
    if (!editorContent) {
      addTerminalLog("error", "No code in editor to compile.");
      return;
    }

    setIsCompiling(true);
    setActiveTab("compiler");
    setCompilerLogs([]);
    addCompilerLog("info", `Initiating compiler pipeline for ${activeFile}...`);
    addCompilerLog("info", "Executing local Abstract Syntax Tree (AST) parsing...");

    try {
      const res = await fetch(`${API_BASE_URL}/compile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: activeFile, source_code: editorContent })
      });

      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          addCompilerLog("stdout", data.logs);
          addCompilerLog("success", `✓ Compilation SUCCESS! WASM binary generated (Base64 size: ${data.wasm_base64.length} chars)`);
          addTerminalLog("success", `✓ ${activeFile} compiled successfully.`);
          toast.success("Compilation successful!");
          setProblems([]);
          setCompiledWasm(data.wasm_base64);
        } else {
          addCompilerLog("stdout", data.logs);
          addCompilerLog("error", "❌ Compilation failed. Correct syntax/type mismatches shown above.");
          addTerminalLog("error", `❌ ${activeFile} compilation failed.`);
          toast.error("Compilation failed. Check errors.");
          setCompiledWasm("");
          
          const parsedProblems = parseCompilerErrors(data.logs);
          setProblems(parsedProblems);
          if (parsedProblems.length > 0) {
            addTerminalLog("error", `Found ${parsedProblems.length} compilation issues. Check the PROBLEMS tab.`);
          }
        }
      } else {
        addCompilerLog("error", "Fatal: Remote compiler server returned 500 error.");
      }
    } catch (err) {
      addCompilerLog("error", "Fatal: Cannot connect to compiler sandbox gateway.");
    } finally {
      setIsCompiling(false);
    }
  };

  const handleDownloadWasm = () => {
    if (!compiledWasm) return;
    try {
      const binaryString = window.atob(compiledWasm);
      const bytes = new Uint8Array(binaryString.length);
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }
      const blob = new Blob([bytes], { type: "application/wasm" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const wasmName = activeFile ? activeFile.replace(/\.py$/, "") + ".wasm" : "contract.wasm";
      a.download = wasmName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      addTerminalLog("success", `✓ Downloaded WASM: ${wasmName}`);
    } catch (err: any) {
      addTerminalLog("error", `Failed to download WASM: ${err.message || err}`);
    }
  };

  const getContractMethods = () => {
    const methods: { name: string; params: { name: string; type: string }[] }[] = [];
    try {
      const lines = editorContent.split("\n");
      lines.forEach(line => {
        const match = line.match(/^\s*def\s+(\w+)\s*\(([^)]*)\)/);
        if (match) {
          const name = match[1];
          if (!name.startsWith("_")) {
            const paramsStr = match[2];
            const params = paramsStr.split(",")
              .map(p => p.trim())
              .filter(p => p && p !== "self")
              .map(p => {
                const parts = p.split(":");
                const pName = parts[0].trim();
                const pType = parts[1] ? parts[1].trim() : "any";
                return { name: pName, type: pType };
              });
            methods.push({ name, params });
          }
        }
      });
    } catch (e) {}
    return methods;
  };

  const handleFuncChange = (funcName: string) => {
    setSelectedFunc(funcName);
    const methods = getContractMethods();
    const target = methods.find(m => m.name === funcName);
    const initialArgs: Record<string, string> = {};
    if (target) {
      target.params.forEach(p => {
        initialArgs[p.name] = "";
      });
    }
    setFuncArgs(initialArgs);
  };

  const handleEditorChange = (val: string) => {
    setEditorContent(val);
    if (activeWorkspace && activeFile) {
      localStorage.setItem(`mycelium_draft_${activeWorkspace}_${activeFile}`, val);
    }
  };

  const handleInvokeCall = async (
    overrideFunc?: string,
    overrideArgs?: Record<string, string>,
    overrideContractId?: string
  ) => {
    if (!isWalletConnected) {
      toast.error("Please connect a Stellar wallet first.");
      return;
    }
    const funcName = overrideFunc || selectedFunc;
    const argsInput = overrideArgs || funcArgs;
    const contractId = overrideContractId || deployedContractId;
    
    if (!funcName) {
      addTerminalLog("error", "No function selected or provided for invocation.");
      return;
    }
    if (!contractId) {
      addTerminalLog("error", "No contract ID provided for invocation.");
      return;
    }
    
    setIsInvoking(true);
    setInvocationLogs(["Initiating transaction call simulation..."]);
    try {
      const StellarSdk = await import("@stellar/stellar-sdk");
      const freighter = await import("@stellar/freighter-api");
      
      const isTestnet = walletNetwork === "TESTNET";
      const rpcUrl = isTestnet ? "https://soroban-testnet.stellar.org" : "https://mainnet.sorobanrpc.com";
      const horizonUrl = isTestnet ? "https://horizon-testnet.stellar.org" : "https://horizon.stellar.org";
      const networkPassphrase = isTestnet ? StellarSdk.Networks.TESTNET : StellarSdk.Networks.PUBLIC;
      
      const server = new StellarSdk.rpc.Server(rpcUrl);

      // Fetch Account info
      const accRes = await fetch(`${horizonUrl}/accounts/${walletAddress}`);
      if (!accRes.ok) {
        throw new Error(`Failed to fetch account info from Horizon: ${accRes.statusText}`);
      }
      const accData = await accRes.json();
      
      const sourceAccount = new StellarSdk.Account(walletAddress, accData.sequence);

      // Convert arguments to ScVals
      const methods = getContractMethods();
      const currentMethod = methods.find(m => m.name === funcName);
      const scValArgs: any[] = [];
      
      if (currentMethod) {
        for (const p of currentMethod.params) {
          const rawVal = argsInput[p.name] !== undefined ? argsInput[p.name] : "";
          try {
            let scVal: any;
            const cleanType = p.type.toLowerCase();
            if (cleanType === "address" || (rawVal.startsWith("G") && rawVal.length === 56) || (rawVal.startsWith("C") && rawVal.length === 56)) {
              scVal = StellarSdk.Address.fromString(rawVal).toScVal();
            } else if (cleanType === "bool" || cleanType === "boolean") {
              scVal = StellarSdk.xdr.ScVal.scvBool(rawVal.toLowerCase() === "true");
            } else if (cleanType === "symbol") {
              scVal = StellarSdk.xdr.ScVal.scvSymbol(rawVal);
            } else if (cleanType === "string") {
              scVal = StellarSdk.xdr.ScVal.scvString(rawVal);
            } else if (cleanType === "i128" || cleanType === "u256" || cleanType === "uint256" || cleanType === "i256" || cleanType === "u128") {
              scVal = StellarSdk.nativeToScVal(BigInt(rawVal), { type: cleanType === "uint256" ? "u256" : cleanType });
            } else {
              if (!isNaN(Number(rawVal)) && rawVal.trim() !== "") {
                scVal = StellarSdk.nativeToScVal(Number(rawVal));
              } else {
                scVal = StellarSdk.xdr.ScVal.scvSymbol(rawVal);
              }
            }
            scValArgs.push(scVal);
          } catch (err: any) {
            throw new Error(`Failed to parse parameter "${p.name}" (${p.type}): ${err.message}`);
          }
        }
      }

      setInvocationLogs(prev => [...prev, "✓ Arguments converted to ScVal values.", "Simulating transaction on-ledger..."]);

      let tx = new StellarSdk.TransactionBuilder(sourceAccount, {
        fee: "1000000",
        networkPassphrase
      })
      .addOperation(StellarSdk.Operation.invokeContractFunction({
        contract: contractId,
        function: funcName,
        args: scValArgs
      }))
      .setTimeout(0)
      .build();

      const simResult = await server.simulateTransaction(tx);
      if ((simResult as any).error) {
        throw new Error(`Simulation failed: ${(simResult as any).error}`);
      }

      setInvocationLogs(prev => [...prev, "✓ Simulation successful.", "Requesting signature from Freighter wallet..."]);
      tx = StellarSdk.rpc.assembleTransaction(tx, simResult).build();
      const txHash = tx.hash().toString("hex");

      const signResult = await freighter.signTransaction(tx.toXDR(), { networkPassphrase });
      const signedTx = StellarSdk.TransactionBuilder.fromXDR(signResult.signedTxXdr, networkPassphrase);

      setInvocationLogs(prev => [...prev, `✓ Transaction signed: ${txHash}`, "Submitting transaction to network..."]);
      const sendRes = await server.sendTransaction(signedTx);
      if (sendRes.status === "ERROR") {
        throw new Error(`Transaction rejected: ${JSON.stringify((sendRes as any).errorResult)}`);
      }

      setInvocationLogs(prev => [...prev, "Waiting for transaction execution check..."]);
      let txStatus = await server.getTransaction(txHash);
      let retries = 15;
      while (txStatus.status === "NOT_FOUND" && retries > 0) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        txStatus = await server.getTransaction(txHash);
        retries--;
      }

      if (txStatus.status !== "SUCCESS") {
        throw new Error(`Transaction execution failed with status: ${txStatus.status}`);
      }

      let resultStr = "Success (void)";
      if (txStatus.resultMetaXdr) {
        try {
          const meta = StellarSdk.xdr.TransactionMeta.fromXDR(txStatus.resultMetaXdr as any, "base64");
          const v3 = (meta as any).v3?.();
          if (v3) {
            const returnVal = v3.sorobanMeta?.()?.returnValue?.();
            if (returnVal) {
              resultStr = `ReturnValue: ${JSON.stringify(StellarSdk.scValToNative(returnVal))}`;
            }
          }
        } catch (e: any) {
          resultStr = `Success (unable to parse returnValue: ${e.message})`;
        }
      }

      setInvocationLogs(prev => [
        ...prev,
        "✅ Transaction Executed successfully!",
        `Result: ${resultStr}`,
        `Transaction Hash: ${txHash}`
      ]);
      addTerminalLog("success", `✓ Contract function [${funcName}] invoked successfully.`);
      toast.success(`Method [${funcName}] called successfully!`);
    } catch (err: any) {
      setInvocationLogs(prev => [...prev, `❌ Error: ${err.message || err}`]);
      addTerminalLog("error", `❌ Failed to invoke contract: ${err.message || err}`);
      toast.error("Failed to invoke contract.");
    } finally {
      setIsInvoking(false);
    }
  };

  // Real Freighter Wallet Integration using Dynamic Import to avoid Next.js SSR crashes
  const connectFreighter = async () => {
    try {
      addTerminalLog("info", "Checking Freighter wallet status...");
      const freighterModule = await import("@stellar/freighter-api");
      const freighter = (freighterModule as any).isConnected ? freighterModule : ((freighterModule as any).default || freighterModule);
      
      const connectionResult = await freighter.isConnected();
      const isAvailable = typeof connectionResult === 'object' && connectionResult !== null 
        ? connectionResult.isConnected 
        : !!connectionResult;
      
      if (!isAvailable) {
        addTerminalLog("error", "Freighter extension not detected in this browser.");
        toast.error("Freighter extension is not installed. Please download it from freighter.app to deploy.");
        return;
      }
      
      addTerminalLog("info", "Requesting account access authorizations...");
      const addressResult = await freighter.requestAccess();
      if (addressResult.error) {
        addTerminalLog("error", `Access denied. Freighter error: ${addressResult.error}`);
        return;
      }
      
      const publicKey = addressResult.address || (addressResult as any).publicKey;
      if (!publicKey) {
        addTerminalLog("error", "Access denied. Ensure Freighter is unlocked and permission is granted.");
        return;
      }

      let networkName = "TESTNET";
      try {
        const networkResult = await freighter.getNetwork();
        if (networkResult.error) {
          addTerminalLog("info", `Freighter network error: ${networkResult.error}. Defaulting to TESTNET.`);
        } else {
          networkName = networkResult.network || "TESTNET";
        }
      } catch (err) {
        console.warn("Failed to retrieve Freighter network config, using testnet default:", err);
      }

      setWalletAddress(publicKey);
      setWalletType("Freighter");
      setWalletNetwork(networkName);
      setIsWalletConnected(true);
      setShowWalletModal(false);
      
      addTerminalLog("success", `✓ Wallet connected! Provider: Freighter, Address: ${publicKey}, Network: ${networkName}`);
    } catch (err: any) {
      addTerminalLog("error", `Freighter integration error: ${err.message || err}`);
    }
  };


  const handleDeploy = async (network: "testnet" | "mainnet" = "testnet") => {
    if (!isWalletConnected) {
      addTerminalLog("error", "Please connect a Stellar wallet before deploying.");
      setShowWalletModal(true);
      return;
    }

    if (!compiledWasm) {
      addTerminalLog("error", "No compiled WASM binary found. Please compile the contract successfully first.");
      setActiveTab("compiler");
      return;
    }

    const isTestnet = network === "testnet";
    const networkLabel = isTestnet ? "Testnet" : "Mainnet";

    setIsDeploying(true);
    setActiveTab("deploy");
    setDeployLogs([]);
    addDeployLog("info", `Initiating real client-side contract deployment sequence on Stellar ${networkLabel}...`);
    
    try {
      addDeployLog("info", "Dynamically loading @stellar/stellar-sdk modules...");
      const StellarSdk = await import("@stellar/stellar-sdk");
      const freighterModule = await import("@stellar/freighter-api");
      const freighter = (freighterModule as any).isConnected ? freighterModule : ((freighterModule as any).default || freighterModule);
      
      const rpcUrl = isTestnet ? "https://soroban-testnet.stellar.org" : "https://mainnet.sorobanrpc.com";
      const horizonUrl = isTestnet ? "https://horizon-testnet.stellar.org" : "https://horizon.stellar.org";
      const networkPassphrase = isTestnet ? StellarSdk.Networks.TESTNET : StellarSdk.Networks.PUBLIC;
      const freighterNetwork = isTestnet ? "TESTNET" : "PUBLIC";
      
      const server = new StellarSdk.rpc.Server(rpcUrl);
      
      const hexToBytes = (hex: string) => {
        const bytes = new Uint8Array(hex.length / 2);
        for (let i = 0; i < hex.length; i += 2) {
          bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
        }
        return bytes;
      };

      const base64ToBytes = (b64: string) => {
        const binaryString = window.atob(b64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }
        return bytes;
      };

      // 1. Upload WASM
      addDeployLog("info", "ℹ️ Simulating install transaction…");
      const wasmBytes = base64ToBytes(compiledWasm);
      
      let accData: any;
      try {
        const accRes = await fetch(`${horizonUrl}/accounts/${walletAddress}`);
        if (accRes.status === 404) {
          addDeployLog("info", "⚠️ Account not found on Testnet. Requesting funding from Friendbot...");
          addTerminalLog("info", `Account ${walletAddress.slice(0, 6)}... not active on Testnet. Funding via Friendbot...`);
          
          const fundRes = await fetch(`https://friendbot.stellar.org/?addr=${walletAddress}`);
          if (fundRes.ok) {
            addDeployLog("success", "✓ Friendbot successfully funded your account with 10,000 XLM!");
            addTerminalLog("success", "✓ Friendbot funded account.");
            // Wait 4 seconds for ledger commit
            await new Promise(resolve => setTimeout(resolve, 4000));
            // Retry fetch
            const accResRetry = await fetch(`${horizonUrl}/accounts/${walletAddress}`);
            if (!accResRetry.ok) {
              throw new Error(`Failed to fetch account info after Friendbot funding: ${accResRetry.statusText}`);
            }
            accData = await accResRetry.json();
          } else {
            throw new Error("Friendbot funding request failed. Please fund your wallet manually.");
          }
        } else if (!accRes.ok) {
          throw new Error(`Failed to fetch account info from Horizon: ${accRes.statusText}`);
        } else {
          accData = await accRes.json();
        }
      } catch (err: any) {
        throw new Error(`Account setup error: ${err.message || err}`);
      }
      
      let sourceAccount = new StellarSdk.Account(walletAddress, accData.sequence);
      
      let txUpload = new StellarSdk.TransactionBuilder(sourceAccount, {
        fee: "1000000",
        networkPassphrase: networkPassphrase
      })
      .addOperation(StellarSdk.Operation.uploadContractWasm({ wasm: wasmBytes }))
      .setTimeout(0)
      .build();

      let simUpload = await server.simulateTransaction(txUpload);
      if ((simUpload as any).error) {
        throw new Error(`Upload simulation failed: ${(simUpload as any).error}`);
      }
      
      txUpload = StellarSdk.rpc.assembleTransaction(txUpload, simUpload).build();
      const toHex = (buf: Uint8Array) => Array.from(buf).map(b => b.toString(16).padStart(2, '0')).join('');
      const uploadHash = toHex(txUpload.hash());
      
      addDeployLog("info", `ℹ️ Signing transaction: ${uploadHash}`);
      const signUploadResult = await freighter.signTransaction(txUpload.toXDR(), { networkPassphrase });
      const signedTxUpload = StellarSdk.TransactionBuilder.fromXDR(signUploadResult.signedTxXdr, networkPassphrase);
      
      addDeployLog("info", "🌎 Submitting install transaction…");
      let sendUploadRes = await server.sendTransaction(signedTxUpload);
      if (sendUploadRes.status === "ERROR") {
        throw new Error(`Upload transaction submission rejected: ${JSON.stringify((sendUploadRes as any).errorResult)}`);
      }
      
      let uploadStatus = await server.getTransaction(uploadHash);
      let retries = 15;
      while (uploadStatus.status === "NOT_FOUND" && retries > 0) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        uploadStatus = await server.getTransaction(uploadHash);
        retries--;
      }
      
      if (uploadStatus.status !== "SUCCESS") {
        throw new Error(`Upload transaction failed with status: ${uploadStatus.status}`);
      }
      
      // Helper to safely get TransactionMeta from resultMetaXdr
      const getTransactionMeta = (metaXdr: any) => {
        if (!metaXdr) return null;
        if (typeof metaXdr === "string") {
          return StellarSdk.xdr.TransactionMeta.fromXDR(metaXdr, "base64");
        }
        return metaXdr;
      };

      // Helper to safely get TransactionResult from resultXdr
      const getTransactionResult = (resultXdr: any) => {
        if (!resultXdr) return null;
        if (typeof resultXdr === "string") {
          return StellarSdk.xdr.TransactionResult.fromXDR(resultXdr, "base64");
        }
        return resultXdr;
      };

      addDeployLog("info", "Extracting WASM code identifier from transaction result...");
      let wasmHash = "";
      try {
        // Option 1: Try reading from top-level returnValue
        const retVal = (uploadStatus as any).returnValue;
        if (retVal && retVal.switch().name === "scvBytes") {
          const hashBytes = retVal.bytes();
          wasmHash = Array.from(new Uint8Array(hashBytes)).map((b: number) => b.toString(16).padStart(2, '0')).join('');
        }
        
        // Option 2: Try parsing from resultMetaXdr
        if (!wasmHash) {
          const resultMeta = (uploadStatus as any).resultMetaXdr;
          if (resultMeta) {
            const meta = getTransactionMeta(resultMeta);
            const v3 = (meta as any).v3?.();
            if (v3) {
              const returnVal = v3.sorobanMeta?.()?.returnValue?.();
              if (returnVal) {
                const hashBytes = (returnVal as any).bytes();
                wasmHash = Array.from(new Uint8Array(hashBytes)).map((b: number) => b.toString(16).padStart(2, '0')).join('');
              }
            }
          }
        }
        
        // Option 3: Try parsing from resultXdr
        if (!wasmHash) {
          const txResult = getTransactionResult(uploadStatus.resultXdr);
          if (txResult) {
            const results = (txResult as any).result().results();
            if (results && results.length > 0) {
              const tr = (results[0] as any).tr();
              const invokeHostFuncResult = (tr as any).invokeHostFunctionResult();
              const successVal = (invokeHostFuncResult as any).success();
              const hashBytes = (successVal as any).bytes();
              wasmHash = Array.from(new Uint8Array(hashBytes)).map((b: number) => b.toString(16).padStart(2, '0')).join('');
            }
          }
        }
      } catch (err: any) {
        throw new Error(`Failed to parse WASM hash from transaction result: ${err.message || err}`);
      }
      
      if (!wasmHash) {
        throw new Error("WASM hash parsing returned an empty identifier");
      }
      
      addDeployLog("success", `✓ WASM successfully uploaded! WASM ID: ${wasmHash}`);
      addDeployLog("info", "ℹ️");

      // 2. Create Contract
      const accRes2 = await fetch(`${horizonUrl}/accounts/${walletAddress}`);
      const accData2 = await accRes2.json();
      sourceAccount = new StellarSdk.Account(walletAddress, accData2.sequence);
      
      const addressInstance = new StellarSdk.Address(walletAddress);
      const wasmHashBytes = hexToBytes(wasmHash);
      
      let txCreate = new StellarSdk.TransactionBuilder(sourceAccount, {
        fee: "1000000",
        networkPassphrase: networkPassphrase
      })
      .addOperation(StellarSdk.Operation.createCustomContract({
        address: addressInstance,
        wasmHash: wasmHashBytes
      }))
      .setTimeout(0)
      .build();

      addDeployLog("info", "ℹ️ Simulating deploy transaction…");
      let simCreate = await server.simulateTransaction(txCreate);
      if ((simCreate as any).error) {
        throw new Error(`Create contract simulation failed: ${(simCreate as any).error}`);
      }
      
      txCreate = StellarSdk.rpc.assembleTransaction(txCreate, simCreate).build();
      const createHash = toHex(txCreate.hash());

      addDeployLog("info", `ℹ️ Transaction hash is ${createHash}`);
      addDeployLog("info", `🔗 https://stellar.expert/explorer/testnet/tx/${createHash}`);
      
      addDeployLog("info", `ℹ️ Signing transaction: ${createHash}`);
      const signCreateResult = await freighter.signTransaction(txCreate.toXDR(), { networkPassphrase });
      const signedTxCreate = StellarSdk.TransactionBuilder.fromXDR(signCreateResult.signedTxXdr, networkPassphrase);
      
      addDeployLog("info", "🌎 Submitting deploy transaction…");
      let sendCreateRes = await server.sendTransaction(signedTxCreate);
      if (sendCreateRes.status === "ERROR") {
        throw new Error(`Instantiation transaction rejected: ${JSON.stringify((sendCreateRes as any).errorResult)}`);
      }
      
      let createStatus = await server.getTransaction(createHash);
      retries = 15;
      while (createStatus.status === "NOT_FOUND" && retries > 0) {
        await new Promise(resolve => setTimeout(resolve, 2000));
        createStatus = await server.getTransaction(createHash);
        retries--;
      }
      
      if (createStatus.status !== "SUCCESS") {
        throw new Error(`Instantiation transaction failed with status: ${createStatus.status}`);
      }
      
      let contractId = "";
      try {
        const retVal = (createStatus as any).returnValue;
        if (retVal && retVal.switch().name === "scvAddress") {
          contractId = StellarSdk.Address.fromScVal(retVal).toString();
        }
        
        if (!contractId) {
          const txResult = getTransactionResult(createStatus.resultXdr);
          if (txResult) {
            const results = (txResult as any).result().results();
            if (results && results.length > 0) {
              const tr = (results[0] as any).tr();
              const invokeHostFuncResult = (tr as any).invokeHostFunctionResult();
              const successVal = (invokeHostFuncResult as any).success();
              contractId = StellarSdk.Address.fromScVal(successVal as any).toString();
            }
          }
        }
      } catch (err: any) {
        throw new Error(`Failed to parse contract ID from transaction result: ${err.message || err}`);
      }
      
      if (!contractId) {
        throw new Error("Contract ID parsing returned empty string");
      }
      setDeployedContractId(contractId);
      setInvocationLogs([]);
      // Switch to interaction tab after successful deploy so user can call methods
      setTimeout(() => setActiveTab("interaction"), 400);
      
      // Find non-private method names in the Python contract to suggest invocation
      const methods: string[] = [];
      try {
        const lines = editorContent.split("\n");
        lines.forEach(line => {
          const match = line.match(/^\s*def\s+(\w+)\s*\(/);
          if (match) {
            const methodName = match[1];
            if (!methodName.startsWith("_") && methodName !== "initialize") {
              methods.push(methodName);
            }
          }
        });
      } catch (e) {}
      const exampleMethod = methods[0] || "get_price";

      addDeployLog("info", `🔗 https://lab.stellar.org/r/testnet/contract/${contractId}`);
      addDeployLog("success", "✅ Deployed!");
      addDeployLog("success", contractId);
      
      addDeployLog("info", "--------------------------------------------------");
      addDeployLog("info", "Check/Invoke from local CLI:");
      addDeployLog("info", `stellar contract invoke --id ${contractId} --source-account <your-account> --network testnet -- ${exampleMethod}`);
      if (methods.length > 1) {
        addDeployLog("info", `Other functions: ${methods.join(", ")}`);
      }
      addDeployLog("info", "--------------------------------------------------");
      
      const timestamp = (createStatus as any).createdAt || new Date().toISOString();
      const blockNum = (createStatus as any).ledger || "N/A";

      addTerminalLog("success", `✓ Contract successfully deployed on ${networkLabel}.`);
      addTerminalLog("info", "--------------------------------------------------");
      addTerminalLog("info", "To invoke this contract from your local CLI:");
      addTerminalLog("info", `  stellar contract invoke \\`);
      addTerminalLog("info", `    --id ${contractId} \\`);
      addTerminalLog("info", `    --source-account <your_key_or_alias> \\`);
      addTerminalLog("info", `    --network testnet \\`);
      addTerminalLog("info", `    -- ${exampleMethod}`);
      if (methods.length > 1) {
        addTerminalLog("info", `  Other functions: ${methods.join(", ")}`);
      }
      addTerminalLog("info", "--------------------------------------------------");
      addTerminalLog("info", "--------------------------------------------------");
      addTerminalLog("info", "Transaction Hash:");
      addTerminalLog("info", `  ${createHash}`);
      addTerminalLog("info", "Status:");
      addTerminalLog("success", "  Success");
      addTerminalLog("info", "Block:");
      addTerminalLog("info", `  ${blockNum}`);
      addTerminalLog("info", "  4 Block Confirmations");
      addTerminalLog("info", "Timestamp:");
      addTerminalLog("info", `  ${timestamp}`);
      addTerminalLog("info", "Sponsored:");
      addTerminalLog("info", "  No");
      addTerminalLog("info", "From:");
      addTerminalLog("info", `  ${walletAddress}`);
      addTerminalLog("info", "To:");
      addTerminalLog("success", `  ${contractId}`);
      addTerminalLog("info", "Value:");
      addTerminalLog("info", "  0.0 XLM");
      addTerminalLog("info", "Transaction Fee:");
      addTerminalLog("info", "  0.1000000 XLM ($0.01)");
      addTerminalLog("info", "Gas Price:");
      addTerminalLog("info", "  100 Stroops/operation (0.00001 XLM)");
      addTerminalLog("info", "--------------------------------------------------");
      toast.success("Contract successfully deployed!");
    } catch (err: any) {
      addDeployLog("error", `❌ Deployment failed:\n${err.message || JSON.stringify(err)}`);
      addTerminalLog("error", `❌ Contract deployment failed.`);
      toast.error("Contract deployment failed.");
    } finally {
      setIsDeploying(false);
    }
  };

  // Register the freshly-deployed agent on the Hive Registry (creation-mode
  // final step). Invokes register_agent via Freighter, mirroring the SDK's
  // HiveClient.register / `mycelium register`. unique_name = the repo name.
  const HIVE_REGISTRY_ADDRESS = "CCHLAG6L4C6ETKD3ZOYE4GRP3VRUB6A2ES6P52VTENXQURL2VFWXI4XC";
  const handleRegisterAgent = async () => {
    if (!isWalletConnected || !walletAddress) {
      toast.error("Connect Freighter first to register.");
      return;
    }
    if (!deployedContractId) {
      toast.error("Deploy the contract before registering.");
      return;
    }
    setIsRegistering(true);
    try {
      const StellarSdk = await import("@stellar/stellar-sdk");
      const freighter = await import("@stellar/freighter-api");

      const isTestnet = walletNetwork !== "PUBLIC";
      const rpcUrl = isTestnet ? "https://soroban-testnet.stellar.org" : "https://mainnet.sorobanrpc.com";
      const horizonUrl = isTestnet ? "https://horizon-testnet.stellar.org" : "https://horizon.stellar.org";
      const networkPassphrase = isTestnet ? StellarSdk.Networks.TESTNET : StellarSdk.Networks.PUBLIC;
      const server = new StellarSdk.rpc.Server(rpcUrl);

      const uniqueName = activeWorkspace;
      addTerminalLog("info", `Registering agent '${uniqueName}' on the Hive Registry…`);

      const accRes = await fetch(`${horizonUrl}/accounts/${walletAddress}`);
      if (!accRes.ok) throw new Error(`Failed to fetch account info: ${accRes.statusText}`);
      const accData = await accRes.json();
      const sourceAccount = new StellarSdk.Account(walletAddress, accData.sequence);

      const enc = (s: string) => StellarSdk.xdr.ScVal.scvBytes(Buffer.from(s, "utf-8"));
      // capability_hash = SHA-256 of the sorted, comma-joined tags (empty here).
      const capHashBuf = Buffer.from(
        new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode("")))
      );
      const endpoint = `https://${uniqueName}.agents.mycelium.sh/api/v1`;

      const args = [
        StellarSdk.xdr.ScVal.scvSymbol(uniqueName),
        StellarSdk.Address.fromString(walletAddress).toScVal(),
        StellarSdk.xdr.ScVal.scvBytes(capHashBuf),
        enc(endpoint),
        enc(""),                       // model
        enc("Autonomous Agent"),       // role
        enc("Created via the Mycelium Web IDE."),  // desc
      ];

      let tx = new StellarSdk.TransactionBuilder(sourceAccount, { fee: "1000000", networkPassphrase })
        .addOperation(StellarSdk.Operation.invokeContractFunction({
          contract: HIVE_REGISTRY_ADDRESS,
          function: "register_agent",
          args,
        }))
        .setTimeout(0)
        .build();

      const simResult = await server.simulateTransaction(tx);
      if ((simResult as any).error) throw new Error(`Simulation failed: ${(simResult as any).error}`);
      tx = StellarSdk.rpc.assembleTransaction(tx, simResult).build();
      const txHash = tx.hash().toString("hex");

      const signResult = await freighter.signTransaction(tx.toXDR(), { networkPassphrase });
      const signedTx = StellarSdk.TransactionBuilder.fromXDR(signResult.signedTxXdr, networkPassphrase);
      const sendRes = await server.sendTransaction(signedTx);
      if (sendRes.status === "ERROR") throw new Error(`Registration rejected: ${JSON.stringify((sendRes as any).errorResult)}`);

      let status = await server.getTransaction(txHash);
      let retries = 15;
      while (status.status === "NOT_FOUND" && retries > 0) {
        await new Promise(r => setTimeout(r, 2000));
        status = await server.getTransaction(txHash);
        retries--;
      }
      if (status.status !== "SUCCESS") throw new Error(`Registration failed with status: ${status.status}`);

      setRegisteredTx(txHash);
      addTerminalLog("success", `✓ Agent '${uniqueName}' registered on the Hive Registry. Tx: ${txHash}`);
      toast.success("Agent registered! It will appear on the Agent Network.");
      setTimeout(() => { window.location.href = "/agent"; }, 1800);
    } catch (err: any) {
      const m = String(err?.message || err);
      addTerminalLog("error", `❌ Registration failed: ${m}`);
      toast.error(m.toLowerCase().includes("nametaken") || m.toLowerCase().includes("taken")
        ? "That agent name is already taken." : "Registration failed.");
    } finally {
      setIsRegistering(false);
    }
  };

  // Ping Stellar Horizon server to show live network metrics
  const pingStellarNetwork = async () => {
    setIsPinging(true);
    const start = Date.now();
    try {
      const res = await fetch("https://horizon-testnet.stellar.org/");
      if (res.ok) {
        const latency = Date.now() - start;
        setNetworkPing(latency);
      } else {
        setNetworkPing(null);
      }
    } catch {
      setNetworkPing(null);
    } finally {
      setIsPinging(false);
    }
  };


  // Helper to copy terminal logs to clipboard
  const copyTerminalOutput = () => {
    let logsText = "";
    const activeLogs = activeTab === "console" ? terminalLogs : activeTab === "compiler" ? compilerLogs : activeTab === "deploy" ? deployLogs : invocationLogs.map(t => ({ type: "info", text: t, time: "" }));
    activeLogs.forEach(log => {
      logsText += `[${log.time}] ${log.text}\n`;
    });
    navigator.clipboard.writeText(logsText);
    toast.success("Terminal logs copied to clipboard!");
  };

  // Helper to download terminal logs
  const downloadTerminalLogs = () => {
    let logsText = "";
    const activeLogs = activeTab === "console" ? terminalLogs : activeTab === "compiler" ? compilerLogs : activeTab === "deploy" ? deployLogs : invocationLogs.map(t => ({ type: "info", text: t, time: "" }));
    activeLogs.forEach(log => {
      logsText += `[${log.time}] ${log.text}\n`;
    });
    const blob = new Blob([logsText], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `mycelium_${activeTab}_logs.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const theme = getThemeColors();

  return (
    <div className="retro-theme" style={{
      display: "flex",
      flexDirection: "column",
      width: "100vw",
      height: "100vh",
      background: "var(--background)",
      color: "var(--foreground)",
      fontFamily: "var(--font-sans)",
      overflow: "hidden",
      position: "relative"
    }}>
      <div className="scanlines"></div>
      <Toaster 
        toastOptions={{ 
          style: { 
            background: "#0c0c0e", 
            color: "#f0f0f0", 
            border: "1px solid #1a1a1e", 
            fontFamily: "var(--font-mono)", 
            fontSize: "0.82rem",
            borderRadius: "2px",
            boxShadow: "0 4px 15px rgba(0,0,0,0.85)"
          } 
        }} 
      />

      {/* ACCESS SHIELD: GitHub Authentication Redirect */}
      {!isAuthenticated ? (
        <div style={{
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          width: "100%",
          height: "100%",
          zIndex: 10,
          background: "#040405",
          color: "#ffffff",
          fontFamily: "var(--font-sans), sans-serif",
          overflow: "hidden"
        }}>
          {/* Subtle grid */}
          <div className="premium-grid" style={{
            position: "absolute",
            top: 0, left: 0, right: 0, bottom: 0,
            pointerEvents: "none",
            zIndex: 0
          }} />

          {/* Glowing background orb */}
          <div className="glow-orb-cyan" style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            width: "500px",
            height: "400px",
            pointerEvents: "none",
            zIndex: 1
          }} />

          {/* Glassmorphic Auth Card */}
          <div style={{
            position: "relative",
            zIndex: 2,
            padding: "48px 40px",
            width: "90%",
            maxWidth: "460px",
            textAlign: "center",
            background: "rgba(4, 4, 5, 0.75)",
            backdropFilter: "blur(16px)",
            WebkitBackdropFilter: "blur(16px)",
            border: "1px solid rgba(255, 255, 255, 0.08)",
            borderRadius: "16px",
            boxShadow: "0 24px 64px rgba(0, 0, 0, 0.7)"
          }}>
            {/* Brand logo/icon */}
            <div style={{
              display: "flex",
              justifyContent: "center",
              marginBottom: "24px"
            }}>
              <div style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: "72px",
                height: "72px",
                borderRadius: "50%",
                background: "rgba(255, 255, 255, 0.02)",
                border: "1px solid rgba(255, 255, 255, 0.10)",
                boxShadow: "inset 0 0 12px rgba(255, 255, 255, 0.05)"
              }}>
                <Shield size={36} style={{ color: "var(--accent-cyan)" }} />
              </div>
            </div>

            {/* Header */}
            <h2 className="font-display" style={{
              fontSize: "2rem",
              fontWeight: 800,
              color: "#ffffff",
              letterSpacing: "-0.04em",
              lineHeight: "1.1",
              marginBottom: "8px"
            }}>
              Access Shield
            </h2>
            <div style={{
              fontSize: "0.68rem",
              fontFamily: "var(--font-mono)",
              color: "var(--accent-cyan)",
              textTransform: "uppercase",
              letterSpacing: "3px",
              fontWeight: "bold",
              marginBottom: "20px"
            }}>
              Restricted Section: Auth Required
            </div>

            {/* Description */}
            <p style={{
              fontSize: "0.92rem",
              lineHeight: "1.65",
              color: "rgba(255, 255, 255, 0.55)",
              marginBottom: "36px",
              fontWeight: 300
            }}>
              Authentication with GitHub is required to synchronize repositories, check AST rules, and compile Python smart contracts to WASM.
            </p>

            {/* Login Button */}
            <button 
              onClick={handleOAuthLoginRedirect}
              className="premium-button-primary"
              style={{
                width: "100%",
                padding: "14px 24px",
                fontSize: "0.88rem",
                fontWeight: 600,
                borderRadius: "8px",
                boxShadow: "0 4px 20px rgba(255, 255, 255, 0.05)"
              }}
            >
              Sign In via GitHub OAuth
              <ArrowRight size={16} />
            </button>
          </div>
        </div>
      ) : (
        /* FULL PLAYGROUND DASHBOARD */
        <>
          {/* TOP HEADER MENU BAR */}
          <header style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "8px 20px",
            borderBottom: "2px solid var(--border-color)",
            background: "var(--panel-bg)",
            zIndex: 5
          }}>
            {/* Logo */}
            <div style={{ display: "flex", alignItems: "center", gap: "15px" }}>
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
              
              {/* Workspace Badge */}
              <div style={{
                fontSize: "0.8rem",
                fontFamily: "var(--font-mono)",
                background: "#0c0c0e",
                border: "1px solid var(--border-color)",
                padding: "2px 8px",
                color: "var(--accent-green)"
              }}>
                REPO: {activeWorkspace || "NONE"}
              </div>

              {/* Agents Link Badge */}
              <Link href="/agent" style={{
                fontSize: "0.8rem",
                fontFamily: "var(--font-mono)",
                background: "#0c0c0e",
                border: "1px solid var(--border-color)",
                padding: "2px 8px",
                color: "var(--accent-cyan)",
                transition: "all 0.2s"
              }}
              onMouseEnter={e => e.currentTarget.style.borderColor = "var(--accent-cyan)"}
              onMouseLeave={e => e.currentTarget.style.borderColor = "var(--border-color)"}
              >
                AGENTS NETWORK
              </Link>

              {/* Bounty Link Badge */}
              <Link href="/bounty" style={{
                fontSize: "0.8rem",
                fontFamily: "var(--font-mono)",
                background: "#0c0c0e",
                border: "1px solid var(--border-color)",
                padding: "2px 8px",
                color: "var(--accent-cyan)",
                transition: "all 0.2s"
              }}
              onMouseEnter={e => e.currentTarget.style.borderColor = "var(--accent-cyan)"}
              onMouseLeave={e => e.currentTarget.style.borderColor = "var(--border-color)"}
              >
                BOUNTY BOARD
              </Link>
              
              {/* Docs Link Badge */}
              <Link href="/docs" style={{
                fontSize: "0.8rem",
                fontFamily: "var(--font-mono)",
                background: "#0c0c0e",
                border: "1px solid var(--border-color)",
                padding: "2px 8px",
                color: "var(--accent-cyan)",
                transition: "all 0.2s"
              }}
              onMouseEnter={e => e.currentTarget.style.borderColor = "var(--accent-cyan)"}
              onMouseLeave={e => e.currentTarget.style.borderColor = "var(--border-color)"}
              >
                DOCUMENTATION
              </Link>
            </div>

            {/* Wallet & User Actions */}
            <div style={{ display: "flex", alignItems: "center", gap: "15px" }}>
              {/* Wallet Button */}
              <button 
                onClick={() => setShowWalletModal(true)}
                className={`btn-retro ${isWalletConnected ? "btn-retro-accent" : ""}`}
                style={{
                  fontSize: "0.9rem",
                  padding: "4px 12px",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px"
                }}
              >
                <Wallet size={16} />
                {isWalletConnected ? `${walletType.toUpperCase()}: ${walletAddress.slice(0, 4)}...${walletAddress.slice(-4)}` : "[ CONNECT WALLET ]"}
              </button>

              {/* User details */}
              <div style={{
                display: "flex",
                alignItems: "center",
                gap: "10px",
                fontFamily: "var(--font-mono)",
                fontSize: "0.8rem",
                borderLeft: "1px solid var(--border-color)",
                paddingLeft: "15px"
              }}>
                {avatarUrl && (
                  <img 
                    src={avatarUrl} 
                    alt="avatar" 
                    style={{ width: "24px", height: "24px", border: "1px solid var(--border-color)" }}
                  />
                )}
                <span style={{ color: "var(--accent-cyan)" }}>{username}</span>
                
                <button 
                  onClick={handleLogout}
                  style={{
                    background: "none",
                    border: "none",
                    color: "var(--accent-red)",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    padding: 0
                  }}
                  title="Sign Out"
                >
                  <LogOut size={16} />
                </button>
              </div>
            </div>
          </header>

          {/* MAIN CONTAINER LAYOUT */}
          <div style={{
            display: "flex",
            flex: 1,
            width: "100%",
            overflow: "hidden"
          }}>
            
            {/* LEFT SIDEBAR: File Tree & Workspace Manager */}
            <aside style={{
              width: `${sidebarWidth}px`,
              flexShrink: 0,
              background: "var(--panel-bg)",
              display: "flex",
              flexDirection: "column",
              overflowY: "auto"
            }}>
              {/* Workspace Selection Section */}
              <div style={{
                padding: "15px",
                borderBottom: "1px solid var(--border-color)"
              }}>
                <div style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: "8px",
                  fontSize: "0.85rem",
                  color: "var(--text-muted)",
                  textTransform: "uppercase"
                }}>
                  <span>Workspaces (Repos)</span>
                  <button 
                    onClick={() => setShowNewWorkspaceModal(true)}
                    style={{
                      background: "none",
                      border: "none",
                      color: "var(--accent-cyan)",
                      cursor: "pointer"
                    }}
                    title="New Repository"
                  >
                    <Plus size={16} />
                  </button>
                </div>
                
                <select 
                  value={activeWorkspace} 
                  onChange={(e) => setActiveWorkspace(e.target.value)}
                  style={{
                    width: "100%",
                    background: "#000",
                    border: "1px solid var(--border-color)",
                    color: "var(--foreground)",
                    padding: "6px",
                    fontFamily: "var(--font-sans)",
                    fontSize: "1rem",
                    outline: "none"
                  }}
                >
                  {workspaces.map(ws => (
                    <option key={ws.name} value={ws.name}>{ws.name}</option>
                  ))}
                  {workspaces.length === 0 && (
                    <option value="">No Repositories</option>
                  )}
                </select>
              </div>

              {/* File Tree Section */}
              <div style={{
                padding: "15px",
                flex: 1,
                display: "flex",
                flexDirection: "column"
              }}>
                <div style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: "10px",
                  fontSize: "0.85rem",
                  color: "var(--text-muted)",
                  textTransform: "uppercase"
                }}>
                  <span>Repo Files</span>
                  {activeWorkspace && (
                    <button 
                      onClick={() => setShowNewFileModal(true)}
                      style={{
                        background: "none",
                        border: "none",
                        color: "var(--accent-cyan)",
                        cursor: "pointer"
                      }}
                      title="New File"
                    >
                      <Plus size={16} />
                    </button>
                  )}
                </div>

                <div style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "4px",
                  overflowY: "auto",
                  flex: 1
                }}>
                  {files.map(fileItem => (
                    <button
                      key={fileItem.name}
                      onClick={() => {
                        setActiveFile(fileItem.name);
                        setActiveFileSha(fileItem.sha);
                      }}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "8px",
                        background: activeFile === fileItem.name ? "#111" : "none",
                        border: activeFile === fileItem.name ? "1px solid var(--border-color)" : "1px solid transparent",
                        color: activeFile === fileItem.name ? "var(--accent-cyan)" : "var(--foreground)",
                        padding: "6px 8px",
                        textAlign: "left",
                        cursor: "pointer",
                        fontFamily: "var(--font-mono)",
                        fontSize: "0.75rem",
                        width: "100%"
                      }}
                    >
                      <FileCode size={14} />
                      <span style={{ textOverflow: "ellipsis", overflow: "hidden", whiteSpace: "nowrap" }}>
                        {fileItem.name}
                      </span>
                    </button>
                  ))}
                  {files.length === 0 && (
                    <div style={{
                      color: "var(--text-muted)",
                      fontSize: "0.9rem",
                      textAlign: "center",
                      marginTop: "20px"
                    }}>
                      No Python Files.
                    </div>
                  )}
                </div>
              </div>

              {/* Template Injector */}
              {activeWorkspace && activeFile && (
                <div style={{
                  padding: "15px",
                  borderTop: "1px solid var(--border-color)",
                  background: "#030303"
                }}>
                  <div style={{
                    fontSize: "0.8rem",
                    color: "var(--text-muted)",
                    marginBottom: "8px",
                    textTransform: "uppercase",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center"
                  }}>
                    <span>Contract Templates</span>
                    <span style={{ fontSize: "0.7rem", color: "var(--accent-green)" }}>
                      {CONTRACT_TEMPLATES.length}
                    </span>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                    <button
                      onClick={() => {
                        setTemplateSearch("");
                        setTemplateCategory("All");
                        setShowTemplateBrowser(true);
                      }}
                      className="btn-retro btn-retro-accent"
                      style={{ fontSize: "0.78rem", padding: "6px", display: "flex", alignItems: "center", justifyContent: "center", gap: "6px" }}
                    >
                      <FileCode size={13} /> Browse Templates
                    </button>
                    <div style={{ fontSize: "0.68rem", color: "var(--text-muted)", lineHeight: 1.4 }}>
                      {CONTRACT_TEMPLATES.length} example contracts verified to compile to Soroban WASM.
                    </div>
                  </div>
                </div>
              )}
            </aside>

            {/* Sidebar Drag Resizer */}
            <div 
              className={`resizer-col ${isResizingSidebar ? "resizing" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                setIsResizingSidebar(true);
                const startX = e.clientX;
                const startWidth = sidebarWidth;
                
                const handleMouseMove = (moveEvent: MouseEvent) => {
                  const newWidth = Math.max(180, Math.min(500, startWidth + (moveEvent.clientX - startX)));
                  setSidebarWidth(newWidth);
                };
                
                const handleMouseUp = () => {
                  setIsResizingSidebar(false);
                  document.removeEventListener("mousemove", handleMouseMove);
                  document.removeEventListener("mouseup", handleMouseUp);
                };
                
                document.addEventListener("mousemove", handleMouseMove);
                document.addEventListener("mouseup", handleMouseUp);
              }}
            />

            {/* CENTER PANEL: Code Editor & Bottom Terminal */}
            <main style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              background: "#000000",
              overflow: "hidden"
            }}>

              {/* Agent-creation guided rail (Write → Compile → Deploy → Register) */}
              {creationMode && (() => {
                const activeStep = !editorContent ? 1 : !compiledWasm ? 2 : !deployedContractId ? 3 : !registeredTx ? 4 : 5;
                return (
                  <div style={{
                    padding: "14px 24px",
                    borderBottom: "1px solid rgba(255, 255, 255, 0.08)",
                    background: "rgba(10, 10, 12, 0.8)",
                    backdropFilter: "blur(16px)",
                    WebkitBackdropFilter: "blur(16px)",
                    display: "flex", alignItems: "center", justifyContent: "space-between", gap: "20px", flexWrap: "wrap",
                    position: "relative",
                    zIndex: 20
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap" }}>
                      <span style={{ fontSize: "0.72rem", fontWeight: 700, color: "#ffffff", letterSpacing: "1.5px", textTransform: "uppercase", fontFamily: "var(--font-mono)" }}>
                        CREATING AGENT: <span style={{ color: "var(--accent-cyan)" }}>{activeWorkspace}</span>
                      </span>
                      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                        {[
                          { n: 1, label: "Write", done: !!editorContent },
                          { n: 2, label: "Compile", done: !!compiledWasm },
                          { n: 3, label: "Deploy", done: !!deployedContractId },
                          { n: 4, label: "Register", done: !!registeredTx },
                        ].map((s, idx) => {
                          const isDone = s.done;
                          const isActive = s.n === activeStep;
                          
                          let circleStyle: React.CSSProperties = {};
                          let textStyle: React.CSSProperties = {};
                          
                          if (isDone) {
                            circleStyle = {
                              background: "var(--accent-green)",
                              color: "#040405",
                              boxShadow: "0 0 10px rgba(15, 159, 120, 0.3)"
                            };
                            textStyle = {
                              color: "#ffffff",
                              fontWeight: 600
                            };
                          } else if (isActive) {
                            circleStyle = {
                              border: "1.5px solid var(--accent-cyan)",
                              color: "var(--accent-cyan)",
                              background: "rgba(0, 150, 199, 0.06)",
                              boxShadow: "0 0 8px rgba(0, 150, 199, 0.3)"
                            };
                            textStyle = {
                              color: "var(--accent-cyan)",
                              fontWeight: 600
                            };
                          } else {
                            circleStyle = {
                              border: "1px solid rgba(255, 255, 255, 0.15)",
                              color: "rgba(255, 255, 255, 0.3)",
                              background: "rgba(255, 255, 255, 0.02)"
                            };
                            textStyle = {
                              color: "rgba(255, 255, 255, 0.3)",
                              fontWeight: 400
                            };
                          }
                          
                          return (
                            <React.Fragment key={s.n}>
                              {idx > 0 && <span style={{ color: "rgba(255, 255, 255, 0.15)", fontSize: "0.8rem" }}>→</span>}
                              <span style={{
                                fontSize: "0.76rem", display: "flex", alignItems: "center", gap: "6px", ...textStyle
                              }}>
                                <span style={{
                                  width: "20px", height: "20px", borderRadius: "50%", fontSize: "0.68rem",
                                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                                  fontWeight: 700, transition: "all 0.3s ease", ...circleStyle
                                }}>{isDone ? "✓" : s.n}</span>
                                {s.label}
                              </span>
                            </React.Fragment>
                          );
                        })}
                      </div>
                    </div>
                    <button
                      onClick={handleRegisterAgent}
                      disabled={isRegistering || !deployedContractId || !!registeredTx}
                      style={{
                        background: registeredTx 
                          ? "rgba(255,255,255,0.03)" 
                          : isRegistering || !deployedContractId
                            ? "rgba(255,255,255,0.02)"
                            : "linear-gradient(135deg, rgba(139, 92, 246, 0.95), rgba(0, 242, 254, 0.95))",
                        border: registeredTx 
                          ? "1px solid rgba(15, 159, 120, 0.3)" 
                          : "1px solid rgba(255, 255, 255, 0.08)", 
                        borderRadius: "6px", 
                        padding: "9px 18px",
                        color: registeredTx 
                          ? "var(--accent-green)" 
                          : isRegistering || !deployedContractId 
                            ? "rgba(255,255,255,0.25)" 
                            : "#000000", 
                        fontWeight: 700, 
                        fontSize: "0.78rem",
                        cursor: deployedContractId && !registeredTx ? "pointer" : "not-allowed",
                        opacity: deployedContractId && !registeredTx ? 1 : 0.6, 
                        whiteSpace: "nowrap",
                        transition: "all 0.2s ease"
                      }}
                    >
                      {registeredTx ? "✓ Registered" : isRegistering ? "Registering…" : "Register on Hive"}
                    </button>
                  </div>
                );
              })()}

              {/* Tool Control Header */}
              <div style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 20px",
                borderBottom: "1px solid var(--border-color)",
                background: "#050505"
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <span style={{ fontSize: "0.9rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
                    {activeFile ? `editing: ${activeFile}` : "No file open"}
                  </span>
                  {activeFile && (
                    <button 
                      onClick={saveActiveFile}
                      style={{
                        background: "none",
                        border: "none",
                        color: "var(--accent-green)",
                        cursor: "pointer",
                        fontSize: "0.75rem",
                        fontFamily: "var(--font-mono)",
                        textDecoration: "underline"
                      }}
                    >
                      [ SAVE & COMMIT ]
                    </button>
                  )}
                </div>

                <div style={{ display: "flex", gap: "10px" }}>
                  {/* Compile CTA */}
                  <button
                    onClick={handleCompile}
                    disabled={isCompiling || !activeFile}
                    className="btn-retro"
                    style={{
                      fontSize: "0.85rem",
                      padding: "4px 14px",
                      display: "flex",
                      alignItems: "center",
                      gap: "6px"
                    }}
                  >
                    {isCompiling ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
                    [ COMPILE ]
                  </button>

                  {/* Download WASM CTA */}
                  {compiledWasm && (
                    <button
                      onClick={handleDownloadWasm}
                      className="btn-retro"
                      style={{
                        fontSize: "0.85rem",
                        padding: "4px 14px",
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                        borderColor: "var(--accent-cyan)",
                        color: "var(--accent-cyan)"
                      }}
                    >
                      <Download size={14} />
                      [ DOWNLOAD WASM ]
                    </button>
                  )}

                  {/* Deploy Testnet CTA */}
                  <button
                    onClick={() => handleDeploy("testnet")}
                    disabled={isDeploying || !activeFile}
                    className="btn-retro btn-retro-accent"
                    style={{
                      fontSize: "0.85rem",
                      padding: "4px 14px",
                      display: "flex",
                      alignItems: "center",
                      gap: "6px"
                    }}
                  >
                    <Cpu size={14} />
                    [ DEPLOY TESTNET ]
                  </button>

                  {/* Deploy Mainnet CTA */}
                  <button
                    onClick={() => handleDeploy("mainnet")}
                    disabled={isDeploying || !activeFile}
                    className="btn-retro"
                    style={{
                      fontSize: "0.85rem",
                      padding: "4px 14px",
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                      borderColor: "#f59e0b",
                      color: "#f59e0b"
                    }}
                  >
                    <Cpu size={14} />
                    [ DEPLOY MAINNET ]
                  </button>
                </div>
              </div>

              {/* Monaco Code Editor Workspace */}
              <div style={{
                flex: 1,
                width: "100%",
                background: "#000000",
                position: "relative",
                display: "flex",
                flexDirection: "column",
                pointerEvents: (isResizingSidebar || isResizingTerminal) ? "none" : "auto"
              }}>
                {hasDraft && (
                  <div style={{
                    background: "#1e1b10",
                    border: "1px solid #d97706",
                    color: "#f59e0b",
                    padding: "8px 12px",
                    fontSize: "0.8rem",
                    fontFamily: "var(--font-mono)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "10px"
                  }}>
                    <span>⚠️ Unsaved draft changes detected for this file from a previous session.</span>
                    <div style={{ display: "flex", gap: "10px" }}>
                      <button
                        onClick={() => {
                          setEditorContent(draftContent);
                          setHasDraft(false);
                          addTerminalLog("success", "✓ Restored unsaved draft content in editor.");
                        }}
                        className="btn-retro btn-retro-accent"
                        style={{ fontSize: "0.75rem", padding: "2px 8px", borderColor: "#d97706", color: "#d97706" }}
                      >
                        [ RESTORE DRAFT ]
                      </button>
                      <button
                        onClick={() => {
                          if (activeWorkspace && activeFile) {
                            localStorage.removeItem(`mycelium_draft_${activeWorkspace}_${activeFile}`);
                          }
                          setHasDraft(false);
                          setDraftContent("");
                          addTerminalLog("info", "Discarded local draft changes.");
                        }}
                        className="btn-retro"
                        style={{ fontSize: "0.75rem", padding: "2px 8px" }}
                      >
                        [ DISCARD DRAFT ]
                      </button>
                    </div>
                  </div>
                )}
                {activeFile ? (
                  <MonacoEditor
                    height="100%"
                    language="python"
                    theme="vs-dark"
                    value={editorContent}
                    onChange={(val) => handleEditorChange(val || "")}
                    onMount={(editor, monaco) => {
                      editorRef.current = editor;
                      
                      // Register autocomplete helpers for Mycelium Python SDK
                      monaco.languages.registerCompletionItemProvider('python', {
                        provideCompletionItems: (model: any, position: any) => {
                          const suggestions = [
                            {
                              label: 'Symbol',
                              kind: monaco.languages.CompletionItemKind.Class,
                              insertText: 'Symbol("${1:name}")',
                              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                              detail: 'Mycelium Symbol type',
                              documentation: 'Represents a Soroban Symbol value.'
                            },
                            {
                              label: 'i128',
                              kind: monaco.languages.CompletionItemKind.Class,
                              insertText: 'i128',
                              detail: 'Mycelium 128-bit integer type',
                              documentation: 'Represents a signed 128-bit integer.'
                            },
                            {
                              label: 'u256',
                              kind: monaco.languages.CompletionItemKind.Class,
                              insertText: 'u256',
                              detail: 'Mycelium 256-bit integer type',
                              documentation: 'Represents an unsigned 256-bit integer.'
                            },
                            {
                              label: 'contract',
                              kind: monaco.languages.CompletionItemKind.Keyword,
                              insertText: '@contract\nclass ${1:MyContract}:\n    ${0}',
                              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                              detail: 'Mycelium contract decorator',
                              documentation: 'Decorates a class to transpile to a Soroban smart contract.'
                            },
                            {
                              label: 'state.instance',
                              kind: monaco.languages.CompletionItemKind.Method,
                              insertText: '@state.instance\ndef ${1:method_name}(self, ${2:args}):\n    ${0}',
                              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                              detail: 'Mycelium instance storage method',
                              documentation: 'Declares an instance-bound smart contract function.'
                            },
                            {
                              label: 'state.persistent',
                              kind: monaco.languages.CompletionItemKind.Method,
                              insertText: '@state.persistent\ndef ${1:method_name}(self, ${2:args}):\n    ${0}',
                              insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                              detail: 'Mycelium persistent storage method',
                              documentation: 'Declares a persistent storage-bound function.'
                            }
                          ];
                          return { suggestions };
                        }
                      });
                    }}
                    options={{
                      fontSize: 16,
                      fontFamily: "var(--font-mono)",
                      minimap: { enabled: false },
                      scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
                      lineHeight: 24,
                      cursorBlinking: "blink",
                      cursorStyle: "block"
                    }}
                    loading={<div style={{ padding: "20px", color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>Loading Monaco Core...</div>}
                  />
                ) : (
                  <div style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    height: "100%",
                    color: "var(--text-muted)",
                    fontSize: "1.2rem"
                  }}>
                    Select or create a Python smart contract file to start coding.
                  </div>
                )}
              </div>

              {/* GOD LEVEL DEVELOPER TERMINAL CONSOLE */}
              <div style={{
                position: isTerminalFullScreen ? "absolute" : "relative",
                bottom: 0,
                left: 0,
                width: "100%",
                height: isTerminalFullScreen ? "calc(100% - 50px)" : `${terminalHeight}px`,
                zIndex: isTerminalFullScreen ? 8 : 1,
                borderTop: isTerminalFullScreen ? `2px solid ${theme.border}` : "none",
                background: theme.bg,
                color: theme.text,
                display: "flex",
                flexDirection: "column",
                boxShadow: "0 -5px 25px rgba(0,0,0,0.98)"
              }}>
                {!isTerminalFullScreen && (
                  <div 
                    className={`resizer-row ${isResizingTerminal ? "resizing" : ""}`}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      setIsResizingTerminal(true);
                      const startY = e.clientY;
                      const startHeight = terminalHeight;
                      
                      const handleMouseMove = (moveEvent: MouseEvent) => {
                        const newHeight = Math.max(120, Math.min(600, startHeight - (moveEvent.clientY - startY)));
                        setTerminalHeight(newHeight);
                      };
                      
                      const handleMouseUp = () => {
                        setIsResizingTerminal(false);
                        document.removeEventListener("mousemove", handleMouseMove);
                        document.removeEventListener("mouseup", handleMouseUp);
                      };
                      
                      document.addEventListener("mousemove", handleMouseMove);
                      document.addEventListener("mouseup", handleMouseUp);
                    }}
                  />
                )}
                {/* Terminal Navigation Tabs */}
                <div style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  background: "#050506",
                  borderBottom: `1px solid ${theme.border}`,
                  padding: "0 10px"
                }}>
                  <div style={{ display: "flex", gap: "2px" }}>
                    {[
                      { id: "console", label: "CONSOLE", icon: <TermIcon size={12} /> },
                      { id: "compiler", label: "COMPILER", icon: <CpuIcon size={12} /> },
                      { id: "deploy", label: "DEPLOY", icon: <Activity size={12} /> },
                      { id: "interaction", label: "INTERACTION", icon: <Zap size={12} />, badge: deployedContractId ? "●" : undefined },
                      { id: "wallet", label: "WALLET", icon: <Wallet size={12} /> },
                      { id: "network", label: "NETWORK", icon: <Wifi size={12} /> },
                      { id: "problems", label: `PROBLEMS (${problems.length})`, icon: <AlertTriangle size={12} /> }
                    ].map(tab => (
                      <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id as any)}
                        style={{
                          background: activeTab === tab.id ? theme.bg : "transparent",
                          border: "1px solid transparent",
                          borderBottom: "none",
                          borderLeftColor: activeTab === tab.id ? theme.border : "transparent",
                          borderRightColor: activeTab === tab.id ? theme.border : "transparent",
                          borderTopColor: activeTab === tab.id ? theme.accent : "transparent",
                          color: activeTab === tab.id ? theme.accent : "var(--text-muted)",
                          padding: "6px 14px",
                          fontFamily: "var(--font-sans)",
                          fontSize: "0.85rem",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px"
                        }}
                      >
                        {tab.id === "problems" ? (
                          <AlertTriangle size={12} color={problems.length > 0 ? "var(--accent-red)" : "var(--text-muted)"} />
                        ) : tab.icon}
                        {tab.label}
                        {(tab as any).badge && (
                          <span style={{ color: "var(--accent-green)", fontSize: "0.6rem", lineHeight: 1 }}>{(tab as any).badge}</span>
                        )}
                      </button>
                    ))}
                  </div>

                  {/* Tab Action Buttons */}
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <button 
                      onClick={copyTerminalOutput}
                      style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "flex", alignItems: "center", gap: "4px" }}
                      title="Copy Logs"
                    >
                      <Copy size={12} />
                      <span style={{ fontSize: "0.7rem", fontFamily: "var(--font-mono)" }}>COPY</span>
                    </button>
                    
                    <button 
                      onClick={downloadTerminalLogs}
                      style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "flex", alignItems: "center", gap: "4px" }}
                      title="Download Logs"
                    >
                      <Download size={12} />
                      <span style={{ fontSize: "0.7rem", fontFamily: "var(--font-mono)" }}>SAVE</span>
                    </button>

                    <button 
                      onClick={() => setIsTerminalFullScreen(!isTerminalFullScreen)}
                      style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "flex", alignItems: "center", gap: "4px" }}
                      title={isTerminalFullScreen ? "Collapse Terminal" : "Maximize Terminal"}
                    >
                      <TerminalSquare size={12} />
                      <span style={{ fontSize: "0.7rem", fontFamily: "var(--font-mono)" }}>
                        {isTerminalFullScreen ? "COLLAPSE" : "MAXIMIZE"}
                      </span>
                    </button>

                    <span style={{ width: "1px", height: "12px", background: theme.border }}></span>

                    <button 
                      onClick={() => {
                        if (activeTab === "console") setTerminalLogs([]);
                        else if (activeTab === "compiler") setCompilerLogs([]);
                        else if (activeTab === "deploy") setDeployLogs([]);
                        else if (activeTab === "interaction") setInvocationLogs([]);
                        else if (activeTab === "problems") setProblems([]);
                      }}
                      style={{
                        background: "none",
                        border: "none",
                        color: "var(--accent-red)",
                        cursor: "pointer",
                        fontSize: "0.7rem",
                        fontFamily: "var(--font-mono)",
                        textDecoration: "underline"
                      }}
                    >
                      CLEAR
                    </button>
                  </div>
                </div>

                {/* Tab Content Display Area */}
                <div style={{
                  flex: 1,
                  padding: "12px",
                  overflowY: "auto",
                  fontFamily: "var(--font-mono)",
                  fontSize: "0.85rem",
                  lineHeight: "1.5",
                  background: theme.bg,
                  color: theme.text,
                  display: "flex",
                  flexDirection: "column",
                  gap: "4px"
                }}>
                  {/* CONSOLE TAB */}
                  {activeTab === "console" && (
                    <>
                      {terminalLogs.map((log, index) => {
                        let color = theme.text;
                        if (log.type === "error") color = "var(--accent-red)";
                        if (log.type === "success") color = "var(--accent-green)";
                        if (log.type === "stdout") color = theme.accent;
                        if (log.type === "info") color = "var(--accent-yellow)";

                        return (
                          <div key={index} style={{ color }}>
                            <span style={{ color: "#444", marginRight: "10px" }}>[{log.time}]</span>
                            <span>{log.text}</span>
                          </div>
                        );
                      })}
                      
                      {/* Interactive prompt form */}
                      <form onSubmit={handleCliSubmit} style={{ display: "flex", alignItems: "center", marginTop: "10px", width: "100%" }}>
                        <span style={{ color: theme.accent, marginRight: "8px", userSelect: "none" }}>{shellPrompt}</span>
                        <input
                          type="text"
                          value={cliInput}
                          onChange={(e) => setCliInput(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "ArrowUp") {
                              e.preventDefault();
                              if (cliHistory.length > 0) {
                                const nextIndex = historyIndex + 1;
                                if (nextIndex < cliHistory.length) {
                                  setHistoryIndex(nextIndex);
                                  setCliInput(cliHistory[nextIndex]);
                                }
                              }
                            } else if (e.key === "ArrowDown") {
                              e.preventDefault();
                              const nextIndex = historyIndex - 1;
                              if (nextIndex >= 0) {
                                setHistoryIndex(nextIndex);
                                setCliInput(cliHistory[nextIndex]);
                              } else {
                                setHistoryIndex(-1);
                                setCliInput("");
                              }
                            }
                          }}
                          placeholder="Type 'help' for command panel..."
                          style={{
                            flex: 1,
                            background: "transparent",
                            border: "none",
                            color: theme.text,
                            outline: "none",
                            fontFamily: "var(--font-mono)",
                            fontSize: "0.85rem",
                            caretColor: theme.caret
                          }}
                        />
                      </form>
                    </>
                  )}

                  {/* COMPILER TAB */}
                  {activeTab === "compiler" && (
                    compilerLogs.length > 0 ? (
                      compilerLogs.map((log, index) => {
                        let color = theme.text;
                        if (log.type === "error") color = "var(--accent-red)";
                        if (log.type === "success") color = "var(--accent-green)";
                        if (log.type === "stdout") color = theme.accent;
                        if (log.type === "info") color = "var(--accent-yellow)";

                        return (
                          <div key={index} style={{ color }}>
                            <span style={{ color: "#444", marginRight: "10px" }}>[{log.time}]</span>
                            {log.type === "stdout" ? (
                              <pre style={{ margin: 0, fontFamily: "var(--font-mono)", whiteSpace: "pre-wrap", color: theme.accent }}>
                                {log.text}
                              </pre>
                            ) : (
                              <span>{log.text}</span>
                            )}
                          </div>
                        );
                      })
                    ) : (
                      <div style={{ color: "var(--text-muted)", padding: "10px", textAlign: "center" }}>
                        No compilation logs. Click [ COMPILE ] to initiate validation checks.
                      </div>
                    )
                  )}

                  {/* DEPLOY TAB */}
                  {activeTab === "deploy" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: "15px", height: "100%" }}>
                      <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "4px" }}>
                        {deployLogs.length > 0 ? (
                          deployLogs.map((log, index) => {
                            let color = theme.text;
                            if (log.type === "error") color = "var(--accent-red)";
                            if (log.type === "success") color = "var(--accent-green)";
                            if (log.type === "info") color = "var(--accent-yellow)";

                            // Detect url inside log text
                            let url = "";
                            if (log.text.includes("https://")) {
                              const match = log.text.match(/https:\/\/[^\s]+/);
                              if (match) {
                                url = match[0];
                              }
                            }

                            return (
                              <div key={index} style={{ color }}>
                                <span style={{ color: "#444", marginRight: "10px" }}>[{log.time}]</span>
                                {log.text.includes("[Soroban Receipt]") || log.text.includes("Receipt") ? (
                                  <pre style={{ margin: 0, fontFamily: "var(--font-mono)", whiteSpace: "pre-wrap", color: "var(--accent-cyan)" }}>
                                    {log.text}
                                  </pre>
                                ) : url ? (
                                  <span style={{ display: "inline-flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                                    <span>{log.text}</span>
                                    <a 
                                      href={url} 
                                      target="_blank" 
                                      rel="noopener noreferrer"
                                      className="btn-retro"
                                      style={{ 
                                        padding: "2px 6px", 
                                        fontSize: "0.75rem", 
                                        textDecoration: "none", 
                                        color: "var(--accent-cyan)", 
                                        borderColor: "var(--accent-cyan)", 
                                        display: "inline-flex", 
                                        alignItems: "center", 
                                        gap: "4px" 
                                      }}
                                    >
                                      OPEN LINK <ExternalLink size={10} />
                                    </a>
                                  </span>
                                ) : (
                                  <span>{log.text}</span>
                                )}
                              </div>
                            );
                          })
                        ) : (
                          <div style={{ color: "var(--text-muted)", padding: "10px", textAlign: "center" }}>
                            No deployment records. Click [ DEPLOY ] to upload target WASM to Stellar Testnet.
                          </div>
                        )}
                      </div>

                    </div>
                  )}

                  {/* INTERACTION TAB */}
                  {activeTab === "interaction" && (
                    <div style={{ display: "flex", flexDirection: "column", gap: "15px", height: "100%", overflowY: "auto" }}>
                      {deployedContractId ? (
                        <>
                          {/* Contract Info Banner */}
                          <div style={{
                            background: "linear-gradient(135deg, #020e08 0%, #010b10 100%)",
                            border: "1px solid var(--accent-green)",
                            borderLeft: "4px solid var(--accent-green)",
                            padding: "12px 16px",
                            borderRadius: "2px"
                          }}>
                            <div style={{ color: "var(--accent-green)", fontSize: "0.7rem", marginBottom: "4px", letterSpacing: "0.1em" }}>✅ CONTRACT DEPLOYED &amp; READY</div>
                            <div style={{ color: "var(--accent-yellow)", fontFamily: "var(--font-mono)", fontSize: "0.78rem", wordBreak: "break-all" }}>{deployedContractId}</div>
                            <div style={{ display: "flex", gap: "10px", marginTop: "8px", flexWrap: "wrap" }}>
                              <a
                                href={`https://stellar.expert/explorer/testnet/contract/${deployedContractId}`}
                                target="_blank" rel="noopener noreferrer"
                                className="btn-retro"
                                style={{ fontSize: "0.7rem", padding: "2px 8px", textDecoration: "none", color: "var(--accent-cyan)", borderColor: "var(--accent-cyan)", display: "inline-flex", alignItems: "center", gap: "4px" }}
                              >
                                STELLAR.EXPERT <ExternalLink size={9} />
                              </a>
                              <a
                                href={`https://lab.stellar.org/r/testnet/contract/${deployedContractId}`}
                                target="_blank" rel="noopener noreferrer"
                                className="btn-retro"
                                style={{ fontSize: "0.7rem", padding: "2px 8px", textDecoration: "none", color: "var(--accent-cyan)", borderColor: "var(--accent-cyan)", display: "inline-flex", alignItems: "center", gap: "4px" }}
                              >
                                STELLAR LAB <ExternalLink size={9} />
                              </a>
                            </div>
                          </div>

                          {/* Function Caller */}
                          <div className="panel-retro" style={{ padding: "15px" }}>
                            <h4 style={{ color: "var(--accent-cyan)", marginBottom: "14px", fontSize: "1rem", display: "flex", alignItems: "center", gap: "8px" }}>
                              <Zap size={14} /> INTERACTIVE CONTRACT CALLER
                            </h4>

                            <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", marginBottom: "14px", alignItems: "center" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                                <span style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>FUNCTION:</span>
                                <select
                                  value={selectedFunc}
                                  onChange={(e) => handleFuncChange(e.target.value)}
                                  style={{
                                    background: "#000000",
                                    border: "1px solid var(--border-color)",
                                    color: "var(--accent-green)",
                                    fontFamily: "var(--font-mono)",
                                    padding: "4px 10px",
                                    outline: "none",
                                    fontSize: "0.85rem"
                                  }}
                                >
                                  {getContractMethods().map((m) => (
                                    <option key={m.name} value={m.name}>{m.name}</option>
                                  ))}
                                </select>
                              </div>
                              <button
                                onClick={() => handleInvokeCall()}
                                disabled={isInvoking || !isWalletConnected}
                                className="btn-retro btn-retro-accent"
                                style={{ padding: "5px 18px", fontSize: "0.85rem", display: "flex", alignItems: "center", gap: "6px" }}
                              >
                                {isInvoking ? <RefreshCw size={13} className="animate-spin" /> : <Zap size={13} />}
                                {isInvoking ? "CALLING..." : "[ CALL ]"}
                              </button>
                              {!isWalletConnected && (
                                <span style={{ color: "var(--accent-red)", fontSize: "0.75rem" }}>⚠ Connect wallet to call</span>
                              )}
                            </div>

                            {/* Arguments */}
                            {selectedFunc && getContractMethods().find((m) => m.name === selectedFunc)?.params.length !== 0 && (
                              <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginBottom: "14px", padding: "12px", background: "#050a0f", border: "1px dashed var(--border-color)" }}>
                                <div style={{ color: "var(--text-muted)", fontSize: "0.72rem", marginBottom: "4px", letterSpacing: "0.08em" }}>ARGUMENTS</div>
                                {getContractMethods()
                                  .find((m) => m.name === selectedFunc)
                                  ?.params.map((p) => (
                                    <div key={p.name} style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                                      <span style={{ color: "var(--accent-yellow)", width: "140px", display: "inline-block", fontSize: "0.78rem", fontFamily: "var(--font-mono)" }}>
                                        {p.name}
                                        <span style={{ color: "var(--text-muted)", fontSize: "0.7rem" }}> :{p.type}</span>
                                      </span>
                                      <input
                                        type="text"
                                        value={funcArgs[p.name] || ""}
                                        onChange={(e) => {
                                          const val = e.target.value;
                                          setFuncArgs((prev) => ({ ...prev, [p.name]: val }));
                                        }}
                                        placeholder={`Enter ${p.type}…`}
                                        style={{
                                          background: "#000000",
                                          border: "1px solid var(--border-color)",
                                          color: "var(--accent-cyan)",
                                          fontFamily: "var(--font-mono)",
                                          padding: "5px 10px",
                                          fontSize: "0.82rem",
                                          flex: 1,
                                          outline: "none"
                                        }}
                                      />
                                    </div>
                                  ))}
                              </div>
                            )}
                            {selectedFunc && getContractMethods().find((m) => m.name === selectedFunc)?.params.length === 0 && (
                              <div style={{ color: "var(--text-muted)", fontSize: "0.78rem", marginBottom: "14px", fontStyle: "italic" }}>No arguments required for this function.</div>
                            )}

                            {/* Output */}
                            <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginBottom: "6px", letterSpacing: "0.08em" }}>OUTPUT LOG</div>
                            <div style={{
                              background: "#020609",
                              border: "1px solid #102030",
                              padding: "10px 12px",
                              fontFamily: "var(--font-mono)",
                              fontSize: "0.78rem",
                              minHeight: "100px",
                              maxHeight: "220px",
                              overflowY: "auto",
                              color: "#7ab3d9"
                            }}>
                              {invocationLogs.length > 0 ? (
                                invocationLogs.map((log, index) => (
                                  <div key={index} style={{
                                    padding: "1px 0",
                                    color: log.startsWith("❌") ? "var(--accent-red)" : log.startsWith("✅") || log.startsWith("Result:") ? "var(--accent-green)" : "#7ab3d9"
                                  }}>
                                    {log}
                                  </div>
                                ))
                              ) : (
                                <span style={{ color: "var(--text-muted)" }}>Select a function and press [ CALL ] to invoke the contract on-chain.</span>
                              )}
                            </div>
                          </div>
                        </>
                      ) : (
                        <div style={{ color: "var(--text-muted)", padding: "30px 10px", textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: "12px" }}>
                          <Zap size={32} style={{ opacity: 0.25 }} />
                          <div style={{ fontSize: "0.9rem" }}>No contract deployed yet.</div>
                          <div style={{ fontSize: "0.78rem" }}>Compile your contract and click <strong style={{ color: "var(--accent-cyan)" }}>[ DEPLOY ]</strong> to get started.</div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* WALLET STATUS TAB */}
                  {activeTab === "wallet" && (
                    <div style={{ padding: "10px", color: theme.text, fontFamily: "var(--font-mono)" }}>
                      <div style={{ fontSize: "1.1rem", color: theme.accent, marginBottom: "15px", borderBottom: `1px dashed ${theme.border}`, paddingBottom: "5px" }}>
                        STELLAR CRYPTOGRAPHIC KEYPAIR INFORMATION:
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                        <div>
                          <span style={{ color: "var(--text-muted)", width: "150px", display: "inline-block" }}>CONNECTION STATUS:</span>
                          <span style={{ color: isWalletConnected ? "var(--accent-green)" : "var(--accent-red)" }}>
                            {isWalletConnected ? "CONNECTED" : "DISCONNECTED"}
                          </span>
                        </div>
                        <div>
                          <span style={{ color: "var(--text-muted)", width: "150px", display: "inline-block" }}>PROVIDER TYPE:</span>
                          <span>{walletType || "N/A"}</span>
                        </div>
                        <div>
                          <span style={{ color: "var(--text-muted)", width: "150px", display: "inline-block" }}>ACTIVE ACCOUNT ID:</span>
                          <span style={{ color: "var(--accent-cyan)" }}>{walletAddress || "N/A"}</span>
                        </div>
                        <div>
                          <span style={{ color: "var(--text-muted)", width: "150px", display: "inline-block" }}>STELLAR NETWORK:</span>
                          <span>{walletNetwork || "N/A"}</span>
                        </div>
                        <div>
                          <span style={{ color: "var(--text-muted)", width: "150px", display: "inline-block" }}>STELLAR XLM BALANCE:</span>
                          <span>{walletBalance}</span>
                        </div>
                      </div>
                      
                      {!isWalletConnected && (
                        <button 
                          onClick={() => setShowWalletModal(true)}
                          className="btn-retro btn-retro-accent"
                          style={{ marginTop: "20px", fontSize: "0.85rem" }}
                        >
                          [ CONNECT WALLET ]
                        </button>
                      )}
                    </div>
                  )}

                  {/* NETWORK DIAGNOSTICS TAB */}
                  {activeTab === "network" && (
                    <div style={{ padding: "10px", color: theme.text, fontFamily: "var(--font-mono)" }}>
                      <div style={{ fontSize: "1.1rem", color: theme.accent, marginBottom: "15px", borderBottom: `1px dashed ${theme.border}`, paddingBottom: "5px" }}>
                        STELLAR TESTNET GATEWAY DIAGNOSTICS:
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                        <div>
                          <span style={{ color: "var(--text-muted)", width: "150px", display: "inline-block" }}>NODE ENDPOINT:</span>
                          <span style={{ color: "var(--accent-cyan)" }}>https://horizon-testnet.stellar.org/</span>
                        </div>
                        <div>
                          <span style={{ color: "var(--text-muted)", width: "150px", display: "inline-block" }}>LATENCY STATUS:</span>
                          <span>
                            {isPinging ? "Pinging..." : networkPing !== null ? `${networkPing} ms` : "Offline/Unreachable"}
                          </span>
                        </div>
                        <div>
                          <span style={{ color: "var(--text-muted)", width: "150px", display: "inline-block" }}>SERVICE STATUS:</span>
                          <span style={{ color: networkPing !== null ? "var(--accent-green)" : "var(--accent-red)" }}>
                            {networkPing !== null ? "OPERATIONAL" : "OUTAGE"}
                          </span>
                        </div>
                      </div>
                      
                      <button 
                        onClick={pingStellarNetwork}
                        disabled={isPinging}
                        className="btn-retro"
                        style={{ marginTop: "20px", fontSize: "0.85rem", display: "flex", alignItems: "center", gap: "6px" }}
                      >
                        <RefreshCw size={12} className={isPinging ? "animate-spin" : ""} />
                        [ RE-CHECK PING ]
                      </button>
                    </div>
                  )}

                  {/* PROBLEMS TAB */}
                  {activeTab === "problems" && (
                    <div style={{ padding: "10px", color: theme.text, fontFamily: "var(--font-mono)" }}>
                      <div style={{ fontSize: "1.1rem", color: theme.accent, marginBottom: "15px", borderBottom: `1px dashed ${theme.border}`, paddingBottom: "5px" }}>
                        COMPILATION AND STATIC ANALYSIS PROBLEMS:
                      </div>
                      
                      {problems.length > 0 ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                          {problems.map((prob, idx) => (
                            <div 
                              key={idx} 
                              style={{ 
                                display: "flex", 
                                alignItems: "center", 
                                justifyContent: "space-between",
                                background: "#0c0202",
                                border: "1px solid #301010",
                                padding: "8px 12px"
                              }}
                            >
                              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                                <AlertTriangle size={14} color="var(--accent-red)" />
                                <span style={{ 
                                  color: "var(--accent-red)", 
                                  fontWeight: "bold",
                                  borderRight: `1px solid ${theme.border}`,
                                  paddingRight: "10px" 
                                }}>
                                  LINE {prob.line}
                                </span>
                                <span style={{ color: theme.text }}>{prob.message}</span>
                              </div>
                              
                              <button 
                                onClick={() => jumpToLine(prob.line)}
                                className="btn-retro btn-retro-accent"
                                style={{ 
                                  fontSize: "0.75rem", 
                                  padding: "3px 8px",
                                  borderColor: "var(--accent-cyan)",
                                  color: "var(--accent-cyan)" 
                                }}
                              >
                                [ JUMP TO LINE ]
                              </button>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div style={{ color: "var(--accent-green)", padding: "10px", textAlign: "center" }}>
                          ✓ No compiler problems detected. Everything is clean.
                        </div>
                      )}
                    </div>
                  )}

                  <div ref={terminalEndRef}></div>
                </div>
              </div>
            </main>
          </div>
        </>
      )}

      {/* CONTRACT TEMPLATE BROWSER */}
      {showTemplateBrowser && (
        <div
          onClick={() => setShowTemplateBrowser(false)}
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            width: "100vw",
            height: "100vh",
            background: "rgba(0,0,0,0.85)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 110,
            padding: "30px"
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="panel-retro"
            style={{
              width: "100%",
              maxWidth: "920px",
              height: "min(80vh, 720px)",
              display: "flex",
              flexDirection: "column",
              padding: "0",
              overflow: "hidden"
            }}
          >
            {/* Header */}
            <div style={{
              padding: "18px 22px",
              borderBottom: "1px solid var(--border-color)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center"
            }}>
              <div>
                <h3 style={{ fontSize: "1.4rem", color: "var(--accent-cyan)", letterSpacing: "1px", margin: 0 }}>
                  CONTRACT TEMPLATES
                </h3>
                <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: "4px", fontFamily: "var(--font-mono)" }}>
                  {CONTRACT_TEMPLATES.length} contracts verified to compile to Soroban WASM
                </div>
              </div>
              <button
                onClick={() => setShowTemplateBrowser(false)}
                className="btn-retro"
                style={{ fontSize: "0.85rem", padding: "4px 12px" }}
              >
                CLOSE
              </button>
            </div>

            {/* Controls */}
            <div style={{ padding: "14px 22px", borderBottom: "1px solid var(--border-color)", display: "flex", flexDirection: "column", gap: "10px" }}>
              <input
                type="text"
                value={templateSearch}
                onChange={(e) => setTemplateSearch(e.target.value)}
                placeholder="Search contracts (e.g. escrow, staking, oracle, nft)..."
                autoFocus
                style={{
                  width: "100%",
                  background: "#000",
                  border: "1px solid var(--border-color)",
                  color: "var(--accent-green)",
                  padding: "9px 12px",
                  fontFamily: "var(--font-mono)",
                  fontSize: "0.9rem",
                  outline: "none"
                }}
              />
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                {["All", ...TEMPLATE_CATEGORIES].map((cat) => {
                  const isActive = templateCategory === cat;
                  return (
                    <button
                      key={cat}
                      onClick={() => setTemplateCategory(cat)}
                      className={`btn-retro ${isActive ? "btn-retro-accent" : ""}`}
                      style={{ fontSize: "0.7rem", padding: "3px 9px" }}
                    >
                      {cat}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Grid */}
            <div style={{ flex: 1, overflowY: "auto", padding: "16px 22px" }}>
              {(() => {
                const q = templateSearch.trim().toLowerCase();
                const filtered = CONTRACT_TEMPLATES.filter((t) => {
                  const matchesCat = templateCategory === "All" || t.category === templateCategory;
                  const matchesSearch =
                    !q ||
                    t.label.toLowerCase().includes(q) ||
                    t.description.toLowerCase().includes(q) ||
                    t.id.toLowerCase().includes(q);
                  return matchesCat && matchesSearch;
                });
                if (filtered.length === 0) {
                  return (
                    <div style={{ textAlign: "center", color: "var(--text-muted)", padding: "40px", fontFamily: "var(--font-mono)", fontSize: "0.85rem" }}>
                      No contracts match &quot;{templateSearch}&quot;.
                    </div>
                  );
                }
                return (
                  <div style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))",
                    gap: "12px"
                  }}>
                    {filtered.map((t) => (
                      <button
                        key={t.id}
                        onClick={() => injectTemplate(t)}
                        style={{
                          textAlign: "left",
                          background: "rgba(255,255,255,0.02)",
                          border: "1px solid var(--border-color)",
                          borderRadius: "6px",
                          padding: "12px 14px",
                          cursor: "pointer",
                          color: "var(--foreground)",
                          display: "flex",
                          flexDirection: "column",
                          gap: "6px",
                          transition: "all 0.15s"
                        }}
                        onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--accent-cyan)"; e.currentTarget.style.background = "rgba(0,242,254,0.05)"; }}
                        onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--border-color)"; e.currentTarget.style.background = "rgba(255,255,255,0.02)"; }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
                          <span style={{ fontSize: "0.92rem", fontWeight: 600, color: "#fff" }}>{t.label}</span>
                          <span style={{
                            fontSize: "0.6rem",
                            fontFamily: "var(--font-mono)",
                            color: "var(--accent-purple)",
                            border: "1px solid var(--border-color)",
                            borderRadius: "10px",
                            padding: "1px 7px",
                            whiteSpace: "nowrap"
                          }}>
                            {t.category}
                          </span>
                        </div>
                        <span style={{ fontSize: "0.72rem", color: "var(--text-muted)", lineHeight: 1.4 }}>
                          {t.description || "Mycelium smart contract."}
                        </span>
                      </button>
                    ))}
                  </div>
                );
              })()}
            </div>
          </div>
        </div>
      )}

      {/* NEW WORKSPACE MODAL */}
      {showNewWorkspaceModal && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          width: "100vw",
          height: "100vh",
          background: "rgba(0,0,0,0.85)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 100
        }}>
          <div className="panel-retro" style={{ padding: "25px", width: "350px" }}>
            <h3 style={{ fontSize: "1.5rem", marginBottom: "15px", color: "var(--accent-cyan)" }}>
              CREATE REPOSITORY
            </h3>
            <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-muted)", marginBottom: "6px", fontFamily: "var(--font-mono)" }}>
              REPOSITORY NAME:
            </label>
            <input 
              type="text" 
              value={newWorkspaceName}
              onChange={(e) => setNewWorkspaceName(e.target.value)}
              placeholder="e.g. mycelium-agent"
              style={{
                width: "100%",
                background: "#000",
                border: "1px solid var(--border-color)",
                color: "var(--accent-green)",
                padding: "8px",
                fontFamily: "var(--font-sans)",
                fontSize: "1.1rem",
                outline: "none",
                marginBottom: "20px"
              }}
            />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button 
                onClick={() => {
                  setShowNewWorkspaceModal(false);
                  setNewWorkspaceName("");
                }} 
                className="btn-retro"
                style={{ fontSize: "0.9rem", padding: "4px 10px" }}
              >
                CANCEL
              </button>
              <button 
                onClick={handleCreateWorkspace}
                className="btn-retro btn-retro-accent"
                style={{ fontSize: "0.9rem", padding: "4px 10px" }}
              >
                CREATE
              </button>
            </div>
          </div>
        </div>
      )}

      {/* NEW FILE MODAL */}
      {showNewFileModal && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          width: "100vw",
          height: "100vh",
          background: "rgba(0,0,0,0.85)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 100
        }}>
          <div className="panel-retro" style={{ padding: "25px", width: "350px" }}>
            <h3 style={{ fontSize: "1.5rem", marginBottom: "15px", color: "var(--accent-cyan)" }}>
              CREATE PYTHON FILE
            </h3>
            <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-muted)", marginBottom: "6px", fontFamily: "var(--font-mono)" }}>
              FILE NAME:
            </label>
            <input 
              type="text" 
              value={newFileName}
              onChange={(e) => setNewFileName(e.target.value)}
              placeholder="e.g. contract.py"
              style={{
                width: "100%",
                background: "#000",
                border: "1px solid var(--border-color)",
                color: "var(--accent-green)",
                padding: "8px",
                fontFamily: "var(--font-sans)",
                fontSize: "1.1rem",
                outline: "none",
                marginBottom: "20px"
              }}
            />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button 
                onClick={() => {
                  setShowNewFileModal(false);
                  setNewFileName("");
                }} 
                className="btn-retro"
                style={{ fontSize: "0.9rem", padding: "4px 10px" }}
              >
                CANCEL
              </button>
              <button 
                onClick={handleCreateFile}
                className="btn-retro btn-retro-accent"
                style={{ fontSize: "0.9rem", padding: "4px 10px" }}
              >
                CREATE
              </button>
            </div>
          </div>
        </div>
      )}

      {/* WALLET SELECTION MODAL */}
      {showWalletModal && (
        <div style={{
          position: "fixed",
          top: 0,
          left: 0,
          width: "100vw",
          height: "100vh",
          background: "rgba(0,0,0,0.85)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          zIndex: 100
        }}>
          <div className="panel-retro" style={{ padding: "30px", width: "400px", textAlign: "center" }}>
            <h3 style={{ fontSize: "1.8rem", marginBottom: "10px", color: "var(--accent-cyan)", letterSpacing: "1px" }}>
              CONNECT STELLAR WALLET
            </h3>
            <div style={{
              fontFamily: "var(--font-mono)",
              fontSize: "0.75rem",
              color: "var(--text-muted)",
              textTransform: "uppercase",
              marginBottom: "20px",
              borderBottom: "1px solid var(--border-color)",
              paddingBottom: "10px"
            }}>
              SELECT COMPATIBLE INTERFACE
            </div>
            
            <div style={{ display: "flex", flexDirection: "column", gap: "15px", marginBottom: "25px" }}>
              {/* Freighter Option */}
              <button 
                onClick={connectFreighter}
                className="btn-retro btn-retro-accent"
                style={{
                  padding: "10px",
                  fontSize: "1.1rem",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between"
                }}
              >
                <span>FREIGHTER WALLET</span>
                <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>[ RECOMMENDED ]</span>
              </button>

              {/* Albedo Option */}
              <button 
                disabled
                className="btn-retro"
                style={{
                  padding: "10px",
                  fontSize: "1.1rem",
                  borderColor: "var(--border-color)",
                  color: "var(--text-muted)",
                  cursor: "not-allowed",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between"
                }}
              >
                <span>ALBEDO LINK</span>
                <span style={{ fontSize: "0.75rem" }}>[ OFFLINE ]</span>
              </button>

            </div>

            <div style={{ display: "flex", justifyContent: "center" }}>
              <button 
                onClick={() => setShowWalletModal(false)}
                className="btn-retro"
                style={{ fontSize: "0.9rem", padding: "4px 15px" }}
              >
                CLOSE
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
