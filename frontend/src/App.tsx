import { useState, useEffect, useCallback } from "react";
import { Sparkles, Radio, RefreshCw, Layers, LogOut, Server, KeyRound, BookOpen, Sun, Moon, Monitor, Volume2, Menu, X, Mic, Sliders, Video, Upload, Wand2 } from "lucide-react";
import { api } from "./api/client";
import { audioEngine } from "./services/audioEngine";

import { VoiceSampleUpload } from "./components/VoiceSampleUpload";
import { VoiceDesignPanel } from "./components/VoiceDesignPanel";
import { PageHeader } from "./components/ui/PageHeader";
import { TTSPanel } from "./components/TTSPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { ApiKeyPanel } from "./components/ApiKeyPanel";
import { ApiDocsPage } from "./components/ApiDocsPage";
import { LoginRegister } from "./components/LoginRegister";
import { AdminDashboard } from "./components/AdminDashboard";
import { JobHistoryPanel } from "./components/JobHistoryPanel";
import { VoiceLibraryPanel } from "./components/VoiceLibraryPanel";
import { AudioPlayer } from "./components/AudioPlayer";
import { PlaygroundPanel } from "./components/PlaygroundPanel";
import { ASRPanel } from "./components/ASRPanel";
import DubbingStudio from "./components/DubbingStudio";
import { ErrorBoundary } from "./components/ui/ErrorBoundary";

type AppTab = "workspace" | "voice-lab" | "library" | "history" | "docs" | "admin" | "playground" | "asr" | "dubbing";

const TAB_TO_HASH: Record<AppTab, string> = {
  workspace: "#/",
  asr: "#/asr",
  dubbing: "#/dubbing",
  library: "#/library",
  "voice-lab": "#/voice-lab",
  history: "#/history",
  docs: "#/docs",
  playground: "#/playground",
  admin: "#/admin",
};

function parseTabFromHash(hash: string): AppTab {
  const clean = (hash || "").split("?")[0].replace(/\/+$/, "");
  switch (clean) {
    case "#/asr":
      return "asr";
    case "#/dubbing":
      return "dubbing";
    case "#/library":
      return "library";
    case "#/voice-lab":
      return "voice-lab";
    case "#/history":
      return "history";
    case "#/docs":
      return "docs";
    case "#/playground":
      return "playground";
    case "#/admin":
      return "admin";
    default:
      return "workspace";
  }
}

const USER_SCOPED_STORAGE_KEYS = [
  "VITE_TTS_JOB_ID",
  "VITE_TTS_DRAFT_TEXT",
  "VITE_TTS_RAW_INPUT_MAP",
  "VITE_VOICE_DESIGN_JOB_ID",
  "VITE_VOICE_DESIGN_PREVIEW_ID",
  "VITE_ASR_PANEL_JOB_ID",
  "VITE_PLAYGROUND_JOB_ID",
  "active_dubbing_job_id",
  "open_dubbing_job_tabs",
];

function getAgentOrbState(
  connectionStatus: "checking" | "connected" | "disconnected",
  kaggleStatus: "unconfigured" | "connected" | "error"
) {
  if (connectionStatus === "disconnected") {
    return {
      colorClass: "bg-destructive",
      pulseClass: "animate-orb-red",
      label: "GPU Agent: Lỗi kết nối",
      desc: "Gateway offline",
    };
  }
  if (kaggleStatus === "unconfigured") {
    return {
      colorClass: "bg-muted-foreground",
      pulseClass: "",
      label: "GPU Agent: Chưa thiết lập",
      desc: "Cần cấu hình Kaggle",
    };
  }
  if (kaggleStatus === "error") {
    return {
      colorClass: "bg-destructive",
      pulseClass: "animate-orb-red",
      label: "GPU Agent: Lỗi kết nối",
      desc: "Kiểm tra thông số Kaggle",
    };
  }
  return {
    colorClass: "bg-success",
    pulseClass: "animate-orb-green",
    label: "GPU Agent: Sẵn sàng",
    desc: "Đang chờ tác vụ mới",
  };
}

function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("VITE_JWT_TOKEN"));
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<AppTab>(() => parseTabFromHash(window.location.hash));
  const [voiceLabMode, setVoiceLabMode] = useState<"design" | "upload">("design");
  const [pendingDubbingJobId, setPendingDubbingJobId] = useState<string | null>(null);
  const [dubbingListNavTrigger, setDubbingListNavTrigger] = useState<number>(0);

  const [theme, setTheme] = useState<"light" | "dark" | "system">(() => {
    return (localStorage.getItem("theme") as "light" | "dark" | "system") || "dark";
  });

  useEffect(() => {
    const root = window.document.documentElement;

    const applyTheme = (themeName: "light" | "dark" | "system") => {
      root.classList.remove("light", "dark");

      if (themeName === "system") {
        const systemTheme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
        root.classList.add(systemTheme);
      } else {
        root.classList.add(themeName);
      }
    };

    applyTheme(theme);
    localStorage.setItem("theme", theme);

    if (theme === "system") {
      const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
      const handleChange = () => applyTheme("system");
      mediaQuery.addEventListener("change", handleChange);
      return () => mediaQuery.removeEventListener("change", handleChange);
    }
  }, [theme]);

  useEffect(() => {
    const handleHashChange = () => {
      setActiveTab(parseTabFromHash(window.location.hash));
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => {
      window.removeEventListener("hashchange", handleHashChange);
    };
  }, []);

  useEffect(() => {
    if (!sidebarOpen) return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [sidebarOpen]);

  const navigateToTab = useCallback((tab: AppTab) => {
    setActiveTab(tab);
    setSidebarOpen(false);
    const targetHash = TAB_TO_HASH[tab] || "#/";
    if (window.location.hash !== targetHash) {
      window.location.hash = targetHash;
    }
    window.scrollTo(0, 0);
  }, []);

  const [currentUser, setCurrentUser] = useState<{ id: string; username: string; is_admin: boolean } | null>(null);
  const [uiLayout, setUiLayout] = useState<"classic" | "modern">("modern");
  const [activeVoiceSampleId, setActiveVoiceSampleId] = useState<string | null>(null);
  const [refreshHistory, setRefreshHistory] = useState(0);
  const handleJobCreatedOrUpdated = useCallback(() => {
    setRefreshHistory(prev => prev + 1);
  }, []);
  const [currentPlayUrl, setCurrentPlayUrl] = useState<string | null>(null);
  const [currentPlayTitle, setCurrentPlayTitle] = useState<string>("");
  const [currentPlayCanRegenerate, setCurrentPlayCanRegenerate] = useState<boolean>(false);
  const [globalPlayerPlaying, setGlobalPlayerPlaying] = useState<boolean>(false);

  const handlePlayAudio = useCallback((url: string, title: string, canRegen = false) => {
    setCurrentPlayUrl(url);
    setCurrentPlayTitle(title);
    setCurrentPlayCanRegenerate(canRegen);
    setGlobalPlayerPlaying(true);
  }, []);

  const handleTogglePlay = useCallback(() => {
    setGlobalPlayerPlaying(prev => !prev);
  }, []);

  const [connectionStatus, setConnectionStatus] = useState<"checking" | "connected" | "disconnected">("checking");
  const [apiBaseUrl] = useState<string>(() => api.getApiBaseUrl() || (typeof window !== "undefined" ? window.location.origin : ""));
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [showApiKeyModal, setShowApiKeyModal] = useState(false);
  const [kaggleStatus, setKaggleStatus] = useState<"unconfigured" | "connected" | "error">("unconfigured");

  const handleLogout = useCallback(() => {
    localStorage.removeItem("VITE_JWT_TOKEN");
    for (const k of USER_SCOPED_STORAGE_KEYS) {
      localStorage.removeItem(k);
    }
    audioEngine.clearCache();
    setToken(null);
    setCurrentUser(null);
    setActiveVoiceSampleId(null);
    setCurrentPlayUrl(null);
    setCurrentPlayTitle("");
    setCurrentPlayCanRegenerate(false);
    setGlobalPlayerPlaying(false);
    setShowSettingsModal(false);
    setShowApiKeyModal(false);
  }, []);

  // Multi-tab auth sync + automatic 401 session expiry handling
  useEffect(() => {
    const onUnauthorized = () => {
      handleLogout();
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === "VITE_JWT_TOKEN") {
        if (!e.newValue) {
          handleLogout();
        } else {
          setToken(e.newValue);
        }
      }
    };
    window.addEventListener("omnivoice:unauthorized", onUnauthorized);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener("omnivoice:unauthorized", onUnauthorized);
      window.removeEventListener("storage", onStorage);
    };
  }, [handleLogout]);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await api.getSettings();
      setUiLayout(res.ui_layout === "classic" ? "classic" : "modern");
      if (!res.kaggle_username || !res.kaggle_key_configured) {
        setKaggleStatus("unconfigured");
      } else {
        setKaggleStatus("connected");
      }
    } catch (err) {
      console.error("Lỗi lấy cấu hình hệ thống:", err);
    }
  }, []);

  const checkHealth = useCallback(async () => {
    try {
      await api.getHealth();
      setConnectionStatus("connected");
    } catch (err) {
      console.error("Health check failed", err);
      setConnectionStatus("disconnected");
    }
  }, []);

  const fetchUser = useCallback(async () => {
    try {
      const res = await api.getMe();
      setCurrentUser({ id: res.id, username: res.username, is_admin: res.is_admin });
    } catch (err) {
      console.error("Lỗi lấy thông tin user:", err);
      handleLogout();
    }
  }, [handleLogout]);

  useEffect(() => {
    let active = true;
    const runCheck = async () => {
      try {
        await api.getHealth();
        if (active) setConnectionStatus("connected");
      } catch {
        if (active) setConnectionStatus("disconnected");
      }
    };
    void runCheck();
    const timer = setInterval(runCheck, 45000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const handleLoginSuccess = useCallback((newToken: string) => {
    localStorage.setItem("VITE_JWT_TOKEN", newToken);
    setToken(newToken);
  }, []);

  useEffect(() => {
    if (!token) return;
    let active = true;
    (async () => {
      try {
        const meRes = await api.getMe();
        if (!active) return;
        setCurrentUser({ id: meRes.id, username: meRes.username, is_admin: meRes.is_admin });
      } catch (err) {
        console.error("Lỗi xác thực phiên đăng nhập:", err);
        if (active) handleLogout();
        return;
      }

      try {
        const settingsRes = await api.getSettings();
        if (!active) return;
        setUiLayout(settingsRes.ui_layout === "classic" ? "classic" : "modern");
        if (!settingsRes.kaggle_username || !settingsRes.kaggle_key_configured) {
          setKaggleStatus("unconfigured");
        } else {
          setKaggleStatus("connected");
        }
      } catch (settingsErr) {
        console.warn("Không thể tải cấu hình người dùng:", settingsErr);
      }
    })();
    return () => {
      active = false;
    };
  }, [token, handleLogout]);

  const effectiveUser = token ? currentUser : null;

  const handleVoiceSampleActive = useCallback((sampleId: string | null) => {
    setActiveVoiceSampleId(sampleId || null);
  }, []);

  if (!token) {
    const hasOAuthParams =
      typeof window !== "undefined" &&
      (window.location.search.includes("code=") || window.location.search.includes("error="));
    if (activeTab === "docs" && !hasOAuthParams) {
      return (
        <ApiDocsPage
          onBack={() => navigateToTab("workspace")}
          isLoggedIn={false}
        />
      );
    }
    return <LoginRegister onLoginSuccess={handleLoginSuccess} />;
  }

  const { colorClass: orbColorClass, pulseClass: orbPulseClass, label: orbLabel, desc: orbDesc } = getAgentOrbState(
    connectionStatus,
    kaggleStatus
  );

  const hasActivePlayer = Boolean(currentPlayUrl);

  const sidebarContent = (
    <div className="flex flex-col h-full bg-card border-r border-border text-foreground">
      {/* Brand logo & compact status */}
      <div className="p-4 border-b border-border flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="bg-primary text-primary-foreground p-2 rounded-lg shrink-0">
            <Radio className="w-4.5 h-4.5" />
          </div>
          <div className="flex flex-col min-w-0">
            <h2 className="text-sm font-bold tracking-tight text-foreground flex items-center gap-1 leading-none">
              <span>OmniVoice</span>
            </h2>
            <span className="text-[9px] text-muted-foreground font-bold uppercase tracking-wider mt-0.5">
              Gateway
            </span>
          </div>
        </div>

        {/* Compact GPU Status Dot */}
        <div className="flex items-center shrink-0" title={`${orbLabel}: ${orbDesc}`}>
          <div className={`w-2.5 h-2.5 rounded-full relative ${orbColorClass} ${orbPulseClass}`} />
        </div>
      </div>

      <nav className="flex-grow p-3 flex flex-col gap-5 overflow-y-auto">
        {/* Main Menu group */}
        <div className="flex flex-col gap-0.5">
          <div className="text-[9px] font-extrabold text-muted-foreground/60 uppercase tracking-widest px-3 mb-1.5">
            Menu
          </div>

          <button
            type="button"
            onClick={() => navigateToTab("workspace")}
            className={`w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold transition-all flex items-center gap-2.5 cursor-pointer ${
              activeTab === "workspace"
                ? "bg-secondary text-foreground shadow-sm font-bold"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
            }`}
          >
            <Sparkles className="w-3.5 h-3.5 shrink-0" />
            <span>Text to Speech</span>
          </button>

          <button
            type="button"
            onClick={() => navigateToTab("asr")}
            className={`w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold transition-all flex items-center gap-2.5 cursor-pointer ${
              activeTab === "asr"
                ? "bg-secondary text-foreground shadow-sm font-bold"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
            }`}
          >
            <Mic className="w-3.5 h-3.5 shrink-0" />
            <span>Speech to Text</span>
          </button>

          <button
            type="button"
            onClick={() => {
              setPendingDubbingJobId(null);
              setDubbingListNavTrigger(prev => prev + 1);
              navigateToTab("dubbing");
            }}
            className={`w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold transition-all flex items-center gap-2.5 cursor-pointer ${
              activeTab === "dubbing"
                ? "bg-secondary text-foreground shadow-sm font-bold"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
            }`}
          >
            <Video className="w-3.5 h-3.5 shrink-0" />
            <span>Video Dubbing</span>
          </button>

          <button
            type="button"
            onClick={() => navigateToTab("library")}
            className={`w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold transition-all flex items-center gap-2.5 cursor-pointer ${
              activeTab === "library"
                ? "bg-secondary text-foreground shadow-sm font-bold"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
            }`}
          >
            <Volume2 className="w-3.5 h-3.5 shrink-0" />
            <span>Voice Library</span>
          </button>

          <button
            type="button"
            onClick={() => navigateToTab("voice-lab")}
            className={`w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold transition-all flex items-center gap-2.5 cursor-pointer ${
              activeTab === "voice-lab"
                ? "bg-secondary text-foreground shadow-sm font-bold"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
            }`}
          >
            <Sliders className="w-3.5 h-3.5 shrink-0" />
            <span>Voice Design</span>
          </button>

          <button
            type="button"
            onClick={() => navigateToTab("history")}
            className={`w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold transition-all flex items-center gap-2.5 cursor-pointer ${
              activeTab === "history"
                ? "bg-secondary text-foreground shadow-sm font-bold"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
            }`}
          >
            <Layers className="w-3.5 h-3.5 shrink-0" />
            <span>History</span>
          </button>

          <button
            type="button"
            onClick={() => navigateToTab("docs")}
            className={`w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold transition-all flex items-center gap-2.5 cursor-pointer ${
              activeTab === "docs"
                ? "bg-secondary text-foreground shadow-sm font-bold"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
            }`}
          >
            <BookOpen className="w-3.5 h-3.5 shrink-0" />
            <span>API Docs</span>
          </button>
        </div>

        {/* Selected Clone Voice section */}
        <div className="flex flex-col gap-1.5 pt-3 border-t border-border/40 shrink-0">
          <div className="text-[9px] font-extrabold text-muted-foreground/60 uppercase tracking-widest px-3">
            Active Voice
          </div>

          <div className="px-1">
            {activeVoiceSampleId ? (
              <div className="bg-secondary/40 border border-border/40 rounded-lg p-2.5 flex items-center justify-between gap-2 shadow-sm">
                <div className="flex items-center gap-2 min-w-0">
                  <div className="bg-primary/10 p-1.5 rounded text-primary shrink-0">
                    <Sparkles className="w-3 h-3 animate-pulse" />
                  </div>
                  <div className="flex flex-col min-w-0">
                    <span className="text-[8px] font-bold text-primary uppercase tracking-wider leading-none">
                      Active
                    </span>
                    <span className="text-[10px] font-mono font-bold text-foreground truncate max-w-[105px] mt-0.5" title={activeVoiceSampleId}>
                      {activeVoiceSampleId}
                    </span>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setActiveVoiceSampleId(null)}
                  className="min-h-[32px] text-[10px] hover:text-foreground text-muted-foreground border border-border bg-card hover:bg-secondary px-2.5 py-1 rounded-md transition-colors cursor-pointer font-bold shrink-0"
                >
                  Clear
                </button>
              </div>
            ) : (
              <div className="text-[10px] text-muted-foreground/80 px-2 py-1 italic">
                No active voice selected
              </div>
            )}
          </div>
        </div>

        {/* System & Configuration menu group */}
        <div className="flex flex-col gap-0.5 pt-3 border-t border-border/40 mt-auto shrink-0">
          <div className="text-[9px] font-extrabold text-muted-foreground/60 uppercase tracking-widest px-3 mb-1.5">
            System
          </div>

          <button
            type="button"
            onClick={() => setShowSettingsModal(true)}
            className="w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold text-muted-foreground hover:text-foreground hover:bg-secondary/40 transition-all flex items-center gap-2.5 cursor-pointer"
          >
            <Server className="w-3.5 h-3.5 shrink-0" />
            <span>Kaggle Settings</span>
          </button>

          <button
            type="button"
            onClick={() => setShowApiKeyModal(true)}
            className="w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold text-muted-foreground hover:text-foreground hover:bg-secondary/40 transition-all flex items-center gap-2.5 cursor-pointer"
          >
            <KeyRound className="w-3.5 h-3.5 shrink-0" />
            <span>API Keys</span>
          </button>

          {currentUser?.is_admin && (
            <>
              <button
                type="button"
                onClick={() => navigateToTab("playground")}
                className={`w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold transition-all flex items-center gap-2.5 cursor-pointer ${
                  activeTab === "playground"
                    ? "bg-secondary text-foreground font-bold"
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
                }`}
              >
                <Sparkles className="w-3.5 h-3.5 shrink-0" />
                <span>Playground - Test API</span>
              </button>

              <button
                type="button"
                onClick={() => navigateToTab("admin")}
                className={`w-full min-h-[40px] px-3 py-2 rounded-lg text-xs font-semibold transition-all flex items-center gap-2.5 cursor-pointer ${
                  activeTab === "admin"
                    ? "bg-secondary text-foreground font-bold"
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary/40"
                }`}
              >
                <Server className="w-3.5 h-3.5 shrink-0" />
                <span>Admin Portal</span>
              </button>
            </>
          )}
        </div>
      </nav>

      {/* User profile & controls */}
      <div className="p-3 border-t border-border bg-secondary/25 flex flex-col gap-2 shrink-0">
        {currentUser && (
          <div className="flex items-center justify-between gap-2 bg-card border border-border px-2.5 py-2 rounded-lg">
            <div className="flex flex-col min-w-0">
              <span className="text-xs font-bold text-foreground truncate">@{currentUser.username}</span>
              <span className="text-[8px] text-muted-foreground font-bold uppercase tracking-wider mt-0.5 truncate">
                {currentUser.is_admin ? "Admin" : "User"}
              </span>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              aria-label="Đăng xuất"
              className="min-w-[44px] min-h-[44px] flex items-center justify-center p-1.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-lg transition-colors cursor-pointer shrink-0"
              title="Đăng xuất"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        )}

        <div className="flex items-center justify-between gap-1 text-[10px] text-muted-foreground px-1">
          <span className="truncate">Status: {connectionStatus === "connected" ? "Online" : "Offline"}</span>

          <div className="flex items-center gap-1">
            <button
              type="button"
              aria-label="Kiểm tra kết nối lại"
              onClick={checkHealth}
              className="min-w-[40px] min-h-[40px] justify-center hover:text-foreground cursor-pointer flex items-center gap-0.5 font-bold rounded-lg hover:bg-secondary/50"
              title="Reload connection"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>

            <button
              type="button"
              aria-label="Chuyển đổi giao diện sáng tối"
              onClick={() => {
                setTheme(prev => {
                  if (prev === "system") return "light";
                  if (prev === "light") return "dark";
                  return "system";
                });
              }}
              className="min-w-[40px] min-h-[40px] flex items-center justify-center text-muted-foreground hover:text-foreground cursor-pointer shrink-0 rounded-lg hover:bg-secondary/50"
              title={`Giao diện: ${theme === "system" ? "Tự động" : theme === "light" ? "Sáng" : "Tối"}`}
            >
              {theme === "system" && <Monitor className="w-3.5 h-3.5" />}
              {theme === "light" && <Sun className="w-3.5 h-3.5 text-warning" />}
              {theme === "dark" && <Moon className="w-3.5 h-3.5 text-primary" />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );

  // Keep fetchUser callable cleanly when child triggers refresh if needed
  void fetchUser;

  return (
    <div className="min-h-screen bg-background text-foreground flex font-sans overflow-x-hidden relative">
      {/* Desktop Sidebar */}
      <aside className="hidden lg:block w-fluid-sidebar h-screen fixed top-0 left-0 shrink-0 z-40">
        {sidebarContent}
      </aside>

      {sidebarOpen && (
        <div className="lg:hidden fixed inset-0 z-[60] flex" role="dialog" aria-modal="true" aria-label="Menu điều hướng">
          <div
            onClick={() => setSidebarOpen(false)}
            className="fixed inset-0 bg-black/60 backdrop-blur-sm"
          />
          <div className="relative w-fluid-sidebar max-w-sm h-full flex-col z-[60] animate-fadeIn">
            {sidebarContent}
            <button
              type="button"
              aria-label="Đóng menu"
              onClick={() => setSidebarOpen(false)}
              className="min-w-[44px] min-h-[44px] flex items-center justify-center absolute top-3 -right-12 p-2 bg-card border border-border text-foreground rounded-xl cursor-pointer"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
      )}

      <div className="flex-grow flex flex-col min-w-0 lg:pl-fluid-sidebar min-h-screen">
        <header className="lg:hidden sticky top-0 z-30 bg-background/85 backdrop-blur-md border-b border-border px-4 py-2.5 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Mở menu điều hướng"
              onClick={() => setSidebarOpen(true)}
              className="min-w-[44px] min-h-[44px] flex items-center justify-center p-2 hover:bg-card border border-border/60 rounded-xl text-foreground cursor-pointer"
            >
              <Menu className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-2">
              <div className="bg-primary text-primary-foreground p-1.5 rounded-lg">
                <Radio className="w-4 h-4" />
              </div>
              <span className="text-sm font-bold tracking-tight text-foreground">OmniVoice</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${connectionStatus === "connected" ? "bg-success" : "bg-destructive"}`} />
            <span className="text-[10px] font-mono text-muted-foreground select-all truncate max-w-[120px]">
              {apiBaseUrl}
            </span>
          </div>
        </header>

        <main className={`flex-grow p-4 md:p-6 lg:p-8 w-full max-w-[1400px] mx-auto flex flex-col gap-6 relative ${hasActivePlayer ? "pb-28" : "pb-10"}`}>
          <SettingsPanel
            isOpen={showSettingsModal}
            onClose={() => setShowSettingsModal(false)}
            onStatusChange={(status) => setKaggleStatus(status)}
          />

          <ApiKeyPanel
            isOpen={showApiKeyModal}
            onClose={() => setShowApiKeyModal(false)}
            onNavigateToDocs={() => {
              setShowApiKeyModal(false);
              navigateToTab("docs");
            }}
          />

          {activeTab === "workspace" && (
            <div className="w-full flex flex-col gap-6">
              <ErrorBoundary fallbackTitle="Lỗi hiển thị Text to Speech">
                <TTSPanel
                  activeVoiceSampleId={activeVoiceSampleId}
                  onSelectVoiceSample={handleVoiceSampleActive}
                  onJobCreatedOrUpdated={handleJobCreatedOrUpdated}
                  layout={uiLayout}
                  currentPlayUrl={currentPlayUrl}
                  globalPlayerPlaying={globalPlayerPlaying}
                  onPlayAudio={(url, title) => handlePlayAudio(url, title, true)}
                  onTogglePlay={handleTogglePlay}
                />
              </ErrorBoundary>
            </div>
          )}

          {activeTab === "voice-lab" && (
            <div className="w-full flex flex-col gap-6 animate-fadeIn">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <PageHeader
                  title="Thiết kế giọng nói"
                  description="Kiến tạo giọng đọc nhân tạo từ mô tả ngôn ngữ tự nhiên với trợ lý LLM hoặc tải lên mẫu âm thanh có sẵn."
                  icon={<Sliders className="w-6 h-6" />}
                />

                <div
                  role="tablist"
                  aria-label="Chế độ tạo giọng mẫu"
                  className="inline-flex bg-card border border-border p-1 rounded-xl shrink-0 self-start sm:self-auto"
                >
                  <button
                    type="button"
                    role="tab"
                    aria-selected={voiceLabMode === "design"}
                    onClick={() => setVoiceLabMode("design")}
                    className={`min-h-[42px] px-4 py-2 rounded-lg text-xs font-bold transition-colors cursor-pointer flex items-center gap-2 ${
                      voiceLabMode === "design"
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Wand2 className="w-3.5 h-3.5" />
                    <span>Thiết kế giọng AI</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={voiceLabMode === "upload"}
                    onClick={() => setVoiceLabMode("upload")}
                    className={`min-h-[42px] px-4 py-2 rounded-lg text-xs font-bold transition-colors cursor-pointer flex items-center gap-2 ${
                      voiceLabMode === "upload"
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Upload className="w-3.5 h-3.5" />
                    <span>Tải lên mẫu âm thanh</span>
                  </button>
                </div>
              </div>

              {voiceLabMode === "design" ? (
                <ErrorBoundary fallbackTitle="Lỗi hiển thị Voice Design">
                  <VoiceDesignPanel
                    onAcceptSuccess={handleVoiceSampleActive}
                    onUseVoiceInTTS={(id) => {
                      setActiveVoiceSampleId(id);
                      navigateToTab("workspace");
                    }}
                    onJobCreatedOrUpdated={handleJobCreatedOrUpdated}
                    layout={uiLayout}
                    currentPlayUrl={currentPlayUrl}
                    globalPlayerPlaying={globalPlayerPlaying}
                    onPlayAudio={(url, title) => handlePlayAudio(url, title, false)}
                    onTogglePlay={handleTogglePlay}
                  />
                </ErrorBoundary>
              ) : (
                <div className="max-w-2xl w-full">
                  <ErrorBoundary fallbackTitle="Lỗi hiển thị Tải Mẫu Giọng">
                    <VoiceSampleUpload onUploadSuccess={handleVoiceSampleActive} layout={uiLayout} />
                  </ErrorBoundary>
                </div>
              )}
            </div>
          )}

          {activeTab === "library" && (
            <div className="w-full animate-fadeIn">
              <ErrorBoundary fallbackTitle="Lỗi hiển thị Voice Library">
                <VoiceLibraryPanel
                  activeVoiceSampleId={activeVoiceSampleId}
                  currentUser={effectiveUser}
                  onUseVoice={(id) => {
                    setActiveVoiceSampleId(id);
                    navigateToTab("workspace");
                  }}
                  onVoiceDeleted={(deletedId) => {
                    if (activeVoiceSampleId === deletedId) {
                      setActiveVoiceSampleId(null);
                    }
                  }}
                  layout={uiLayout}
                  currentPlayUrl={currentPlayUrl}
                  globalPlayerPlaying={globalPlayerPlaying}
                  onPlayAudio={(url, title) => handlePlayAudio(url, title, false)}
                  onTogglePlay={handleTogglePlay}
                />
              </ErrorBoundary>
            </div>
          )}

          {activeTab === "history" && (
            <div className="w-full animate-fadeIn">
              <ErrorBoundary fallbackTitle="Lỗi hiển thị History">
                <JobHistoryPanel
                  refreshTrigger={refreshHistory}
                  layout={uiLayout}
                  currentPlayUrl={currentPlayUrl}
                  globalPlayerPlaying={globalPlayerPlaying}
                  onPlayAudio={(url, title) => handlePlayAudio(url, title, false)}
                  onTogglePlay={handleTogglePlay}
                  onClosePlayerIfUrl={(deletedUrl) => {
                    if (currentPlayUrl && deletedUrl && currentPlayUrl.includes(deletedUrl)) {
                      setCurrentPlayUrl(null);
                      setGlobalPlayerPlaying(false);
                    }
                  }}
                  onOpenDubbingJob={(dubId) => {
                    localStorage.setItem("active_dubbing_job_id", dubId);
                    setPendingDubbingJobId(dubId);
                    navigateToTab("dubbing");
                  }}
                />
              </ErrorBoundary>
            </div>
          )}

          {activeTab === "docs" && (
            <div className="w-full animate-fadeIn">
              <ErrorBoundary fallbackTitle="Lỗi hiển thị API Docs">
                <ApiDocsPage onBack={() => navigateToTab("workspace")} isLoggedIn={true} />
              </ErrorBoundary>
            </div>
          )}

          {activeTab === "admin" && effectiveUser?.is_admin && (
            <div className="w-full animate-fadeIn">
              <ErrorBoundary fallbackTitle="Lỗi hiển thị Admin Portal">
                <AdminDashboard onBack={() => navigateToTab("workspace")} onSettingsChanged={fetchSettings} />
              </ErrorBoundary>
            </div>
          )}

          {activeTab === "playground" && effectiveUser?.is_admin && (
            <div className="w-full animate-fadeIn">
              <ErrorBoundary fallbackTitle="Lỗi hiển thị Playground">
                <PlaygroundPanel />
              </ErrorBoundary>
            </div>
          )}

          {activeTab === "asr" && (
            <div className="w-full animate-fadeIn">
              <ErrorBoundary fallbackTitle="Lỗi hiển thị nhận dạng giọng nói (ASR)">
                <ASRPanel />
              </ErrorBoundary>
            </div>
          )}

          {activeTab === "dubbing" && (
            <div className="w-full animate-fadeIn">
              <ErrorBoundary fallbackTitle="Lỗi hiển thị Video Dubbing">
                <DubbingStudio
                  currentUser={effectiveUser}
                  initialJobIdProp={pendingDubbingJobId}
                  onInitialJobConsumed={() => setPendingDubbingJobId(null)}
                  listNavTrigger={dubbingListNavTrigger}
                />
              </ErrorBoundary>
            </div>
          )}
        </main>

        <footer className={`border-t border-border/60 py-6 text-center text-[10px] text-muted-foreground font-semibold bg-background/20 mt-auto ${hasActivePlayer ? "pb-24" : ""}`}>
          OmniVoice On-Demand Gateway MVP &copy; {new Date().getFullYear()} &nbsp;•&nbsp; Built for High-Performance Audio Synthesis
        </footer>
      </div>

      {/* Global Sticky Bottom Audio Player — rendered ONLY when a track is loaded */}
      {hasActivePlayer && (
        <div className="fixed bottom-0 left-0 lg:left-fluid-sidebar right-0 z-40 h-fluid-player bg-card/95 backdrop-blur-md border-t border-border shadow-lg">
          <AudioPlayer
            url={currentPlayUrl || ""}
            title={currentPlayTitle || ""}
            isPlayingGlobal={globalPlayerPlaying}
            onPlayingGlobalChange={setGlobalPlayerPlaying}
            canRegenerate={currentPlayCanRegenerate && activeTab === "workspace"}
            onClose={() => {
              setCurrentPlayUrl(null);
              setCurrentPlayCanRegenerate(false);
              setGlobalPlayerPlaying(false);
            }}
          />
        </div>
      )}
    </div>
  );
}

export default App;
