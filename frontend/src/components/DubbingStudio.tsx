import React, { useState, useEffect, useRef, useCallback } from "react";
import { 
  Sparkles, 
  CheckCircle2, 
  Clock, 
  AlertCircle, 
  Download, 
  FileText, 
  RefreshCw, 
  Save, 
  Key, 
  Sliders, 
  Wand2, 
  Loader2,
  Film,
  Music,
  Mic,
  Languages,
  Upload,
  PlaySquare,
  Cookie,
  ShieldCheck,
  Trash2,
  X,
  ExternalLink,
  Copy,
  Check,
  LogOut,
  Globe,
  Plus,
  Search,
  Eye,
  Play,
  Layers,
  Zap,
  Volume2,
  VolumeX,
  RotateCcw,
  AlertTriangle,
  Edit3,
  ChevronLeft,
  ChevronRight,
  ArrowUpDown,
  FolderOpen
} from "lucide-react";
import { api, getErrorMessage, type VideoDubbingJobResponse, type SubtitleSegment, type LLMProfilePublic, type YouTubeVideoInfo, type VoiceSampleResponse } from "../api/client";
import { PageHeader } from "./ui/PageHeader";
import { SectionCard } from "./ui/SectionCard";

interface DubbingStudioProps {
  currentUser?: { id: string; username: string; is_admin: boolean } | null;
  initialJobIdProp?: string | null;
  onInitialJobConsumed?: () => void;
}

interface ProxyTestResult {
  hash?: string;
  status: "online" | "offline";
  latency_ms: number;
  ip?: string;
  country?: string;
  city?: string;
  org?: string;
  message: string;
}

// Pipeline steps definition
const PIPELINE_STEPS = [
  { id: "downloading", label: "Tải Video", desc: "Tải xuống video & trích xuất audio", icon: Download },
  { id: "separating_audio", label: "Tách Âm Thanh", desc: "Demucs GPU tách Vocals & BGM", icon: Music },
  { id: "transcribing", label: "Whisper ASR", desc: "Trích xuất phụ đề & mốc thời gian", icon: Mic },
  { id: "translating", label: "Dịch Thuật LLM", desc: "Dịch phụ đề qua AI Model", icon: Languages },
  { id: "awaiting_review", label: "Kiểm Duyệt", desc: "Xem trước & chỉnh sửa phụ đề", icon: FileText },
  { id: "generating_tts", label: "Sinh Giọng Clone", desc: "Tổng hợp thoại qua OmniVoice", icon: Wand2 },
  { id: "mixing_audio", label: "Trộn Âm Thanh", desc: "Khớp nhịp & trộn nhạc nền BGM", icon: Sliders },
  { id: "muxing_video", label: "Đóng Gói", desc: "Xuất video lồng tiếng MP4", icon: Film }
];

function getYouTubeId(url?: string | null): string {
  if (!url) return "";
  const match = url.match(
    /(?:https?:\/\/)?(?:(?:www|m|music)\.)?(?:youtu\.be\/|youtube(?:-nocookie)?\.com\/(?:shorts\/|live\/|embed\/|v\/|watch\?(?:.*&)?v=))([0-9A-Za-z_-]{11})/
  );
  return match ? match[1] : "";
}

function formatDuration(seconds?: number | null): string {
  if (seconds === undefined || seconds === null || !Number.isFinite(seconds) || seconds <= 0) return "";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

function estimateDubbingSpokenStats(text: string, targetLanguage?: string): { syllables: number; estSeconds: number } {
  const cleaned = (text || "").replace(/\[[^\[\]\n]{1,64}\]/g, " ").trim();
  if (!cleaned) return { syllables: 0, estSeconds: 0 };

  const lang = (targetLanguage || "vietnamese").toLowerCase();
  const isVi = lang === "vi" || lang.includes("viet") || lang.includes("việt");
  if (!isVi) {
    const words = cleaned.split(/\s+/).filter(Boolean).length;
    const pauses = (cleaned.match(/[,;:]/g) || []).length;
    return { syllables: words, estSeconds: Math.round((words / 2.65 + pauses * 0.18 + 0.08) * 10) / 10 };
  }

  // Approximate phonetic expansion matching backend vietnamese_normalizer.py
  let s = cleaned
    .replace(/(\d)\s*(?:N[\s·.\-]*cm|NCM)\b/g, "$1 niu tơn xen ti mét")
    .replace(/\bN[·.]?cm\b/g, "niu tơn xen ti mét")
    .replace(/(\d)\s*(?:N[·.]m|Nm)\b/g, "$1 niu tơn mét")
    .replace(/(\d)\s*RPM\b/gi, "$1 vòng một phút")
    .replace(/\bvòng\s*\/\s*phút\b/gi, "vòng một phút")
    .replace(/(\d)\s*mm\b/g, "$1 mi li mét")
    .replace(/(\d)\s*cm\b/g, "$1 xen ti mét")
    .replace(/(\d)\s*km\b/g, "$1 ki lô mét")
    .replace(/(\d)\s*m[lL]\b/g, "$1 mi li lít")
    .replace(/(\d)\s*°\s*C\b/gi, "$1 độ xê")
    .replace(/(\d)\s*°/g, "$1 độ");

  const tokens = s.split(/[\s\-–—/]+/).filter(Boolean);
  let syllables = 0;
  for (const rawTok of tokens) {
    const tok = rawTok.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
    if (!tok) continue;
    if (/[^\x00-\x7F]/.test(tok)) {
      syllables += 1;
    } else if (/^\d+(?:[.,]\d+)?$/.test(tok)) {
      const n = Number(tok.replace(",", "."));
      if (Number.isFinite(n)) {
        if (tok.includes(".") || tok.includes(",")) syllables += 3;
        else if (n >= 1000) syllables += 4;
        else if (n >= 100) syllables += 2;
        else if (n >= 20) syllables += 2;
        else syllables += 1;
      } else {
        syllables += 2;
      }
    } else if (/^[A-Z]{2,5}$/.test(tok)) {
      syllables += tok.length;
    } else {
      const w = tok.toLowerCase();
      const groups = w.match(/[aeiouy]+/g) || [];
      let sc = Math.max(1, groups.length);
      if (w.endsWith("e") && !/(?:le|ye|ee|oe|ae)$/.test(w) && sc > 1) sc -= 1;
      syllables += Math.max(1, sc);
    }
  }
  const commas = (s.match(/[,;:]/g) || []).length;
  const internalBreaks = (s.match(/[.!?]+(?=\s+\S)/g) || []).length;
  // Empirically calibrated on native OmniVoice Vietnamese narration at speed=0.92 (sub-linear speed response)
  const est = (0.2015 * syllables + 0.2174 * commas + 0.34 * internalBreaks + 0.0544) * Math.pow(0.96 / 0.92, 0.55);
  return { syllables, estSeconds: Math.round(Math.max(0.25, est) * 10) / 10 };
}

export default function DubbingStudio({
  currentUser,
  initialJobIdProp,
  onInitialJobConsumed,
}: DubbingStudioProps) {
  const isAdmin = Boolean(currentUser?.is_admin);
  // Navigation / Multi-project states: 'create' | 'workspace' | 'list'
  const [viewMode, setViewMode] = useState<"create" | "workspace" | "list">(() => {
    return localStorage.getItem("active_dubbing_job_id") ? "workspace" : "create";
  });
  const [allJobs, setAllJobs] = useState<VideoDubbingJobResponse[]>([]);
  const [loadingAllJobs, setLoadingAllJobs] = useState(false);
  const [jobsFilter, setJobsFilter] = useState<string>("all");
  const [jobsSearch, setJobsSearch] = useState<string>("");
  const [jobsSort, setJobsSort] = useState<"newest" | "oldest" | "review_first" | "running_first">("newest");
  const [jobsPage, setJobsPage] = useState<number>(1);
  const [jobsPageSize, setJobsPageSize] = useState<number>(15);
  const [selectedJobIds, setSelectedJobIds] = useState<string[]>([]);
  const [bulkDeleting, setBulkDeleting] = useState<boolean>(false);

  // Monotonic sequence & recent mutation guards to prevent stale background polls from overwriting new/deleted jobs
  const jobsFetchSeqRef = useRef<number>(0);
  const activeJobFetchIdRef = useRef<string | null>(null);
  const recentlyCreatedJobsRef = useRef<Map<string, { job: VideoDubbingJobResponse; ts: number }>>(new Map());
  const recentlyDeletedIdsRef = useRef<Map<string, number>>(new Map());

  const [openJobTabs, setOpenJobTabs] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem("open_dubbing_job_tabs");
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed)) return parsed.slice(0, 8);
      }
      const single = localStorage.getItem("active_dubbing_job_id");
      return single ? [single] : [];
    } catch {
      return [];
    }
  });

  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [targetLanguage, setTargetLanguage] = useState("Vietnamese"); // Default to Vietnamese
  const [smartSeparation, setSmartSeparation] = useState(true);
  
  // Upload States
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadedJobId, setUploadedJobId] = useState<string | null>(null);
  const [uploadedVideoUrl, setUploadedVideoUrl] = useState<string | null>(null);
  const [uploadedVideoTitle, setUploadedVideoTitle] = useState<string | null>(null);

  // YouTube Preview, Quality & Pre-Download States
  const [ytInfo, setYtInfo] = useState<YouTubeVideoInfo | null>(null);
  const [ytInfoLoading, setYtInfoLoading] = useState(false);
  const [ytInfoError, setYtInfoError] = useState<string | null>(null);
  const [preferredQuality, setPreferredQuality] = useState<string>("1080p");
  const [ytDownloading, setYtDownloading] = useState(false);
  const [ytDownloadJobId, setYtDownloadJobId] = useState<string | null>(null);
  const [ytDownloadProgress, setYtDownloadProgress] = useState(0);
  const [ytDownloadMessage, setYtDownloadMessage] = useState<string>("");
  const ytPreDownloadPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [retryingJob, setRetryingJob] = useState(false);
  const [retryQuality, setRetryQuality] = useState<string>("1080p");
  const [pollRevision, setPollRevision] = useState(0);

  // Diagnostic Logs States
  const [jobLogs, setJobLogs] = useState("");
  const logsEndRef = useRef<HTMLDivElement>(null);

  // Job States
  const [jobId, setJobId] = useState<string | null>(() => {
    return localStorage.getItem("active_dubbing_job_id") || null;
  });
  const [job, setJob] = useState<VideoDubbingJobResponse | null>(null);
  const [loadingJobDetails, setLoadingJobDetails] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // LLM Config state
  const [llmProvider, setLlmProvider] = useState("gemini");
  const [llmModel, setLlmModel] = useState("gemini-2.5-flash");
  const [llmCustomEndpoint, setLlmCustomEndpoint] = useState("");
  const [llmProfiles, setLlmProfiles] = useState<LLMProfilePublic[]>([]);
  const [selectedLlmProfileId, setSelectedLlmProfileId] = useState<string>("");

  // Native Vietnamese Voice Selection state (prevents cross-lingual foreign accent)
  const [voiceSamples, setVoiceSamples] = useState<VoiceSampleResponse[]>([]);
  const [voiceMode, setVoiceMode] = useState<"auto_native" | "voice_sample" | "clone_original">("auto_native");
  const [selectedVoiceSampleId, setSelectedVoiceSampleId] = useState<string>("");
  const [previewingVoiceId, setPreviewingVoiceId] = useState<string | null>(null);
  const voicePreviewAudioRef = useRef<HTMLAudioElement | null>(null);

  // Subtitle editor state
  const [originalSubs, setOriginalSubs] = useState<SubtitleSegment[]>([]);
  const [translatedSubs, setTranslatedSubs] = useState<SubtitleSegment[]>([]);
  const [selectedSegId, setSelectedSegId] = useState<number | null>(null);
  const [savingSubs, setSavingSubs] = useState(false);
  const [retranslating, setRetranslating] = useState(false);
  const [currentVideoTime, setCurrentVideoTime] = useState<number>(0);
  const [subOverlayMode, setSubOverlayMode] = useState<"bilingual" | "en" | "vi" | "off">("bilingual");
  const [editingOrigSegId, setEditingOrigSegId] = useState<number | null>(null);

  // Media Player Refs & Audio Mixer
  const videoPlayerRef = useRef<HTMLVideoElement>(null);
  const vocalsPlayerRef = useRef<HTMLAudioElement>(null);
  const bgmPlayerRef = useRef<HTMLAudioElement>(null);

  const [vocalsVolume, setVocalsVolume] = useState(1.0);
  const [bgmVolume, setBgmVolume] = useState(0.35);
  const [vocalsMuted, setVocalsMuted] = useState(false);
  const [bgmMuted, setBgmMuted] = useState(false);
  const [reseparating, setReseparating] = useState(false);

  // New: AI Subtitle Shortening, Re-Edit & Fast Remix states
  const [shorteningSegId, setShorteningSegId] = useState<number | null>(null);
  const [reEditing, setReEditing] = useState(false);
  const [remixing, setRemixing] = useState(false);
  const [remixVocalVol, setRemixVocalVol] = useState(1.2);
  const [remixBgmVol, setRemixBgmVol] = useState(0.3);
  const [outputVideoTimestamp, setOutputVideoTimestamp] = useState<number>(() => Date.now());
  const [showRemixPanel, setShowRemixPanel] = useState(false);

  // YouTube Authentication States (OAuth, Cookie, Residential Proxy Pool)
  const [activeModalTab, setActiveModalTab] = useState<"proxy" | "oauth" | "cookies">("proxy");
  const [oauthStatus, setOauthStatus] = useState<{ connected: boolean; expires_at?: number } | null>(null);
  const [oauthFlowData, setOauthFlowData] = useState<{ user_code: string; device_code: string; verification_url: string; interval: number } | null>(null);
  const [oauthStarting, setOauthStarting] = useState(false);
  const [oauthPolling, setOauthPolling] = useState(false);
  const [oauthCopied, setOauthCopied] = useState(false);
  const [oauthError, setOauthError] = useState<string | null>(null);
  const [oauthSuccessMsg, setOauthSuccessMsg] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [cookieStatus, setCookieStatus] = useState<{
    has_cookies: boolean;
    is_authenticated?: boolean;
    cookie_count?: number;
    size_bytes?: number;
  } | null>(null);
  const [showCookieModal, setShowCookieModal] = useState(false);
  const [cookieText, setCookieText] = useState("");
  const [cookieUploading, setCookieUploading] = useState(false);
  const [cookieMsg, setCookieMsg] = useState<string | null>(null);
  const [ytDiagTesting, setYtDiagTesting] = useState(false);
  const [ytDiagResult, setYtDiagResult] = useState<{ status: string; message: string } | null>(null);
  const [bridgeStatus, setBridgeStatus] = useState<{
    configured: boolean;
    bridge_online: boolean;
    bridge_detail: string;
    r2_online: boolean;
    r2_detail: string;
    bridge_url?: string | null;
    r2_gateway_url?: string | null;
  } | null>(null);

  // Proxy Pool States
  const [proxyPool, setProxyPool] = useState<Array<{
    hash: string;
    masked: string;
    scheme: string;
    host: string;
    port: string;
    has_auth: boolean;
  }>>([]);
  const [proxyBatchInput, setProxyBatchInput] = useState("");
  const [proxyAdding, setProxyAdding] = useState(false);
  const [proxyTestingAll, setProxyTestingAll] = useState(false);
  const [proxyTestResults, setProxyTestResults] = useState<Record<string, ProxyTestResult>>({});
  const [proxyPoolMsg, setProxyPoolMsg] = useState<string | null>(null);

  const applyJobState = useCallback((data: VideoDubbingJobResponse) => {
    setJob(data);
    if (data.preferred_quality) setRetryQuality(data.preferred_quality);
    if (typeof data.vocals_volume === "number") setVocalsVolume(Math.min(1, Math.max(0, data.vocals_volume)));
    if (typeof data.bgm_volume === "number") setBgmVolume(Math.min(1, Math.max(0, data.bgm_volume)));
    if (data.voice_mode === "auto_native" || data.voice_mode === "voice_sample" || data.voice_mode === "clone_original") {
      setVoiceMode(data.voice_mode);
    } else {
      setVoiceMode("auto_native");
    }
    setSelectedVoiceSampleId(data.voice_sample_id || "");
    setOriginalSubs(Array.isArray(data.original_subtitles) ? data.original_subtitles : []);
    setTranslatedSubs(Array.isArray(data.translated_subtitles) ? data.translated_subtitles : []);
  }, []);

  const fetchAllJobs = useCallback(async (showLoading = false) => {
    const reqSeq = ++jobsFetchSeqRef.current;
    if (showLoading) setLoadingAllJobs(true);
    try {
      const res = await api.listDubbingJobs(1, 200);
      // Ignore stale in-flight poll responses superseded by a newer fetch, create, or delete
      if (reqSeq !== jobsFetchSeqRef.current) return;

      const now = Date.now();
      for (const [k, ts] of recentlyDeletedIdsRef.current.entries()) {
        if (now - ts > 15000) recentlyDeletedIdsRef.current.delete(k);
      }
      for (const [k, entry] of recentlyCreatedJobsRef.current.entries()) {
        if (now - entry.ts > 30000) recentlyCreatedJobsRef.current.delete(k);
      }

      const rawList = Array.isArray(res.jobs) ? res.jobs : [];
      const filteredServer = rawList.filter(j => !recentlyDeletedIdsRef.current.has(j.id));
      const serverIds = new Set(filteredServer.map(j => j.id));

      // Keep any freshly created project visible even if an overlapping server query missed it
      const pendingCreated: VideoDubbingJobResponse[] = [];
      for (const [k, entry] of recentlyCreatedJobsRef.current.entries()) {
        if (serverIds.has(k)) {
          recentlyCreatedJobsRef.current.delete(k);
        } else if (!recentlyDeletedIdsRef.current.has(k)) {
          pendingCreated.push(entry.job);
        }
      }

      const jobsList = [...pendingCreated, ...filteredServer];
      setAllJobs(jobsList);

      const validIds = new Set(jobsList.map(j => j.id));

      // Only prune truly deleted tabs — never auto-flood the tab bar with old awaiting_review projects
      setOpenJobTabs(prev => {
        const surviving = prev.filter(id => validIds.has(id)).slice(0, 8);
        localStorage.setItem("open_dubbing_job_tabs", JSON.stringify(surviving));
        return surviving;
      });

      // Keep active workspace job hydrated without ever hijacking/jumping to another project
      setJobId(currId => {
        if (currId && validIds.has(currId)) {
          const matched = jobsList.find(j => j.id === currId);
          if (matched) {
            setJob(prevJob => {
              if (!prevJob || prevJob.id !== currId) {
                setOriginalSubs(Array.isArray(matched.original_subtitles) ? matched.original_subtitles : []);
                setTranslatedSubs(Array.isArray(matched.translated_subtitles) ? matched.translated_subtitles : []);
                return matched;
              }
              return prevJob;
            });
          }
        }
        return currId;
      });
    } catch (err: unknown) {
      console.error("Lỗi lấy danh sách dự án:", err);
    } finally {
      if (showLoading) setLoadingAllJobs(false);
    }
  }, []);

  const fetchOAuthStatus = useCallback(async () => {
    try {
      const res = await api.getYouTubeOAuthStatus();
      setOauthStatus(res);
    } catch (err: unknown) {
      console.debug("OAuth status unavailable:", err);
    }
  }, []);

  const fetchCookieStatus = useCallback(async () => {
    try {
      const res = await api.getDubbingCookieStatus();
      setCookieStatus(res);
    } catch (err: unknown) {
      console.debug("Cookie status unavailable:", err);
    }
  }, []);

  const fetchProxyPool = useCallback(async () => {
    try {
      const res = await api.getProxyPool();
      setProxyPool(res.proxies || []);
    } catch (err: unknown) {
      console.debug("Proxy pool unavailable:", err);
    }
  }, []);

  const fetchBridgeStatus = useCallback(async () => {
    try {
      const res = await api.getYouTubeBridgeStatus();
      setBridgeStatus(res);
    } catch (err: unknown) {
      console.debug("Bridge status unavailable:", err);
    }
  }, []);

  const fetchLlmProfiles = useCallback(async () => {
    try {
      const profiles = await api.listDubbingLlmProfiles();
      setLlmProfiles(profiles);
      const activeProf = profiles.find(p => p.is_active);
      if (activeProf) {
        setSelectedLlmProfileId(activeProf.id);
      } else if (profiles.length > 0) {
        setSelectedLlmProfileId(profiles[0].id);
      }
    } catch (err: unknown) {
      console.debug("LLM profiles unavailable:", err);
    }
  }, []);

  const fetchVoiceSamples = useCallback(async () => {
    try {
      const samples = await api.listVoiceSamples();
      setVoiceSamples(Array.isArray(samples) ? samples : []);
    } catch (err: unknown) {
      console.debug("Voice samples unavailable:", err);
    }
  }, []);

  const toggleVoicePreview = useCallback((sampleId: string) => {
    if (!sampleId) return;
    if (previewingVoiceId === sampleId && voicePreviewAudioRef.current) {
      voicePreviewAudioRef.current.pause();
      voicePreviewAudioRef.current = null;
      setPreviewingVoiceId(null);
      return;
    }
    if (voicePreviewAudioRef.current) {
      voicePreviewAudioRef.current.pause();
      voicePreviewAudioRef.current = null;
    }
    const audio = new Audio(api.getVoiceSampleAudioUrl(sampleId));
    voicePreviewAudioRef.current = audio;
    setPreviewingVoiceId(sampleId);
    audio.onended = () => {
      setPreviewingVoiceId(prev => (prev === sampleId ? null : prev));
    };
    audio.onerror = () => {
      setPreviewingVoiceId(prev => (prev === sampleId ? null : prev));
    };
    void audio.play().catch(() => {
      setPreviewingVoiceId(null);
    });
  }, [previewingVoiceId]);

  const handleVoiceSelectChange = useCallback((rawVal: string) => {
    if (voicePreviewAudioRef.current) {
      voicePreviewAudioRef.current.pause();
      voicePreviewAudioRef.current = null;
      setPreviewingVoiceId(null);
    }
    if (rawVal === "clone_original") {
      setVoiceMode("clone_original");
      setSelectedVoiceSampleId("");
    } else if (rawVal.startsWith("sample:")) {
      const id = rawVal.slice("sample:".length);
      setVoiceMode("voice_sample");
      setSelectedVoiceSampleId(id);
    } else {
      setVoiceMode("auto_native");
      setSelectedVoiceSampleId("");
    }
  }, []);

  const fetchSystemLlmSettings = useCallback(async () => {
    try {
      const settings = await api.adminGetSystemSettings();
      if (settings.llm_provider) setLlmProvider(settings.llm_provider);
      if (settings.llm_model) setLlmModel(settings.llm_model);
      if (settings.llm_custom_endpoint) setLlmCustomEndpoint(settings.llm_custom_endpoint);
    } catch (err: unknown) {
      console.debug("System LLM settings unavailable:", err);
    }
  }, []);

  const fetchJobDetails = useCallback(async (id: string) => {
    activeJobFetchIdRef.current = id;
    setLoadingJobDetails(true);
    try {
      const data = await api.getDubbingJob(id);
      if (activeJobFetchIdRef.current !== id) return;
      setError(null);
      applyJobState(data);
      setAllJobs(prev => {
        const exists = prev.some(item => item.id === data.id);
        return exists ? prev.map(item => (item.id === data.id ? data : item)) : [data, ...prev];
      });

      // Fetch logs
      try {
        const logData = await api.getDubbingJobLog(id);
        if (activeJobFetchIdRef.current === id) {
          setJobLogs(logData.log);
        }
      } catch {
        // Log file may not exist yet for queued jobs
      }
    } catch (err: unknown) {
      if (activeJobFetchIdRef.current !== id) return;
      console.error("Lỗi lấy thông tin Job:", err);
      const msg = getErrorMessage(err, "Không thể tải chi tiết dự án lồng tiếng.");
      setError(`Không thể mở dự án #${id.slice(0, 8)}: ${msg}`);
    } finally {
      if (activeJobFetchIdRef.current === id) {
        setLoadingJobDetails(false);
      }
    }
  }, [applyJobState]);

  const openProjectInWorkspace = useCallback((targetId: string, cached?: VideoDubbingJobResponse | null) => {
    if (!targetId) return;
    activeJobFetchIdRef.current = targetId;
    setError(null);
    setSelectedSegId(null);
    setOpenJobTabs(prev => {
      const next = [targetId, ...prev.filter(id => id !== targetId)].slice(0, 8);
      localStorage.setItem("open_dubbing_job_tabs", JSON.stringify(next));
      return next;
    });
    setJobId(targetId);
    setViewMode("workspace");
    localStorage.setItem("active_dubbing_job_id", targetId);

    const seed = cached || allJobs.find(j => j.id === targetId) || null;
    if (seed) {
      applyJobState(seed);
    } else {
      setJob(prev => (prev?.id === targetId ? prev : null));
      setOriginalSubs([]);
      setTranslatedSubs([]);
      setJobLogs("");
    }
    setOutputVideoTimestamp(Date.now());
    setPollRevision(r => r + 1);
    void fetchJobDetails(targetId);
  }, [allJobs, applyJobState, fetchJobDetails]);

  useEffect(() => {
    if (!initialJobIdProp) return;
    openProjectInWorkspace(initialJobIdProp);
    onInitialJobConsumed?.();
  }, [initialJobIdProp, openProjectInWorkspace, onInitialJobConsumed]);

  // Restore active job from localStorage on mount & fetch settings & auth statuses
  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (cancelled) return;
      void fetchLlmProfiles();
      void fetchVoiceSamples();
      void fetchAllJobs(false);

      const initialJobId = localStorage.getItem("active_dubbing_job_id");
      if (initialJobId) {
        void fetchJobDetails(initialJobId);
      }

      if (isAdmin) {
        void fetchSystemLlmSettings();
        void fetchCookieStatus();
        void fetchProxyPool();
        void fetchBridgeStatus();

        const savedOauth = localStorage.getItem("youtube_oauth_token");
        if (savedOauth) {
          try {
            const parsed = JSON.parse(savedOauth);
            api.syncYouTubeOAuth(parsed)
              .then(() => fetchOAuthStatus())
              .catch(() => fetchOAuthStatus());
          } catch {
            void fetchOAuthStatus();
          }
        } else {
          void fetchOAuthStatus();
        }
      }
    });

    // Periodic background poll for all jobs
    const allJobsInterval = setInterval(() => {
      void fetchAllJobs(false);
    }, 4000);

    return () => {
      cancelled = true;
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
      if (ytPreDownloadPollRef.current) clearInterval(ytPreDownloadPollRef.current);
      if (voicePreviewAudioRef.current) {
        voicePreviewAudioRef.current.pause();
        voicePreviewAudioRef.current = null;
      }
      clearInterval(allJobsInterval);
    };
  }, [
    isAdmin,
    fetchAllJobs,
    fetchCookieStatus,
    fetchJobDetails,
    fetchLlmProfiles,
    fetchVoiceSamples,
    fetchOAuthStatus,
    fetchProxyPool,
    fetchSystemLlmSettings,
  ]);

  useEffect(() => {
    if (!showCookieModal) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setShowCookieModal(false);
        setCookieMsg(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [showCookieModal]);

  const handleStartOAuth = async () => {
    setOauthStarting(true);
    setOauthError(null);
    setOauthSuccessMsg(null);
    if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    try {
      const data = await api.startYouTubeOAuth();
      setOauthFlowData(data);
      setOauthPolling(true);

      // Start polling
      const pollInterval = (data.interval || 5) * 1000;
      pollTimerRef.current = setInterval(async () => {
        try {
          const pollRes = await api.pollYouTubeOAuth(data.device_code);
          if (pollRes.status === "success") {
            if (pollTimerRef.current) clearInterval(pollTimerRef.current);
            setOauthPolling(false);
            if (pollRes.token_data) {
              localStorage.setItem("youtube_oauth_token", JSON.stringify(pollRes.token_data));
            }
            setOauthSuccessMsg(pollRes.message || "Đã kết nối tài khoản YouTube thành công!");
            void fetchOAuthStatus();
          } else if (pollRes.status === "error") {
            if (pollTimerRef.current) clearInterval(pollTimerRef.current);
            setOauthPolling(false);
            setOauthError(pollRes.error || "Quá trình xác thực không thành công.");
          }
        } catch (err: unknown) {
          // Keep polling on minor network hiccups
          console.debug("OAuth polling transient error:", err);
        }
      }, pollInterval);
    } catch (err: unknown) {
      setOauthError(getErrorMessage(err, "Không thể khởi tạo phiên đăng nhập Google"));
    } finally {
      setOauthStarting(false);
    }
  };

  const handleDisconnectOAuth = async () => {
    if (!window.confirm("Bạn có chắc chắn muốn ngắt kết nối tài khoản YouTube?")) return;
    try {
      await api.disconnectYouTubeOAuth();
      localStorage.removeItem("youtube_oauth_token");
      setOauthStatus({ connected: false });
      setOauthFlowData(null);
      setOauthSuccessMsg(null);
    } catch (err: unknown) {
      alert(`Lỗi: ${getErrorMessage(err, "Không thể ngắt kết nối")}`);
    }
  };

  const handleCopyOAuthCode = () => {
    if (!oauthFlowData?.user_code) return;
    navigator.clipboard.writeText(oauthFlowData.user_code);
    setOauthCopied(true);
    setTimeout(() => setOauthCopied(false), 2500);
  };

  const handleUploadCookieFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setCookieUploading(true);
    setCookieMsg(null);
    try {
      const res = await api.uploadDubbingCookies(file);
      setCookieMsg(res.message);
      void fetchCookieStatus();
    } catch (err: unknown) {
      setCookieMsg(`Lỗi: ${getErrorMessage(err, "Không thể tải cookie")}`);
    } finally {
      setCookieUploading(false);
    }
  };

  const handleSaveCookie = async () => {
    if (!cookieText.trim()) return;
    setCookieUploading(true);
    setCookieMsg(null);
    try {
      const res = await api.uploadDubbingCookies(undefined, cookieText.trim());
      setCookieMsg(res.message);
      setCookieText("");
      void fetchCookieStatus();
    } catch (err: unknown) {
      setCookieMsg(`Lỗi: ${getErrorMessage(err, "Không thể lưu cookie")}`);
    } finally {
      setCookieUploading(false);
    }
  };

  const handleDeleteCookie = async () => {
    if (!window.confirm("Bạn có chắc chắn muốn xóa file cookies đã lưu?")) return;
    setCookieUploading(true);
    setCookieMsg(null);
    try {
      const res = await api.deleteDubbingCookies();
      setCookieMsg(res.message);
      void fetchCookieStatus();
    } catch (err: unknown) {
      setCookieMsg(`Lỗi: ${getErrorMessage(err, "Không thể xóa cookie")}`);
    } finally {
      setCookieUploading(false);
    }
  };

  const handleTestAllProxies = async () => {
    setProxyTestingAll(true);
    setProxyPoolMsg(null);
    try {
      const res = await api.testAllProxiesInPool();
      const map: Record<string, ProxyTestResult> = {};
      for (const r of res.results) {
        map[r.hash] = r;
      }
      setProxyTestResults(map);
      const onlineCount = res.results.filter((r) => r.status === "online").length;
      setProxyPoolMsg(`Kiểm tra hoàn tất: ${onlineCount}/${res.results.length} Proxy đang hoạt động tốt.`);
    } catch (err: unknown) {
      setProxyPoolMsg(`Lỗi kiểm tra proxy: ${getErrorMessage(err, "Timeout")}`);
    } finally {
      setProxyTestingAll(false);
    }
  };

  const handleAddProxyPool = async () => {
    const text = proxyBatchInput.trim();
    if (!text) return;
    setProxyAdding(true);
    setProxyPoolMsg(null);
    try {
      const res = await api.addProxyPool(text);
      setProxyPool(res.proxies || []);
      setProxyBatchInput("");
      setProxyPoolMsg(res.message);
      // Auto-trigger test for newly added proxies
      setTimeout(() => {
        void handleTestAllProxies();
      }, 400);
    } catch (err: unknown) {
      setProxyPoolMsg(`Lỗi: ${getErrorMessage(err, "Không thể thêm proxy vào pool")}`);
    } finally {
      setProxyAdding(false);
    }
  };

  const handleDeleteProxyItem = async (hash: string) => {
    try {
      const res = await api.deleteProxyFromPool(hash);
      setProxyPool(res.proxies || []);
      setProxyTestResults((prev) => {
        const next = { ...prev };
        delete next[hash];
        return next;
      });
      setProxyPoolMsg("Đã xóa proxy khỏi danh sách.");
    } catch (err: unknown) {
      setProxyPoolMsg(`Lỗi: ${getErrorMessage(err, "Không thể xóa")}`);
    }
  };

  const handleClearProxyPool = async () => {
    if (!window.confirm("Bạn có chắc chắn muốn xóa toàn bộ danh sách Proxy không?")) return;
    try {
      await api.clearProxyPool();
      setProxyPool([]);
      setProxyTestResults({});
      setProxyPoolMsg("Đã làm trống toàn bộ danh sách Proxy.");
    } catch (err: unknown) {
      setProxyPoolMsg(`Lỗi: ${getErrorMessage(err, "Không thể xóa")}`);
    }
  };

  // Poll active job status and logs
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    localStorage.setItem("active_dubbing_job_id", jobId);

    // Initial fetch logs
    const fetchLogs = async () => {
      try {
        const logData = await api.getDubbingJobLog(jobId);
        if (!cancelled) setJobLogs(logData.log);
      } catch {
        // Log may not be initialized yet
      }
    };
    void fetchLogs();

    const interval = setInterval(async () => {
      try {
        const data = await api.getDubbingJob(jobId);
        if (cancelled) return;
        setJob(prev => {
          const wasReview = prev?.status === "awaiting_review";
          const isNowReview = data.status === "awaiting_review";
          if (!wasReview || !isNowReview) {
            if (data.original_subtitles && data.original_subtitles.length > 0) {
              setOriginalSubs(data.original_subtitles);
            }
            if (data.translated_subtitles && data.translated_subtitles.length > 0) {
              setTranslatedSubs(data.translated_subtitles);
            }
          }
          return data;
        });

        // Fetch logs
        try {
          const logData = await api.getDubbingJobLog(jobId);
          if (!cancelled) setJobLogs(logData.log);
        } catch {
          // Ignore transient log poll errors
        }

        if (data.status === "completed" || data.status === "failed") {
          clearInterval(interval);
        }
      } catch (err: unknown) {
        if (!cancelled) console.error("Lỗi đồng bộ Job status:", err);
      }
    }, 2500);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [jobId, pollRevision]);

  // Autoscroll logs terminal to bottom
  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [jobLogs]);

  const handleCheckYouTubeInfo = useCallback(async (urlOverride?: string) => {
    const target = (urlOverride ?? youtubeUrl).trim();
    if (!target) {
      setYtInfo(null);
      setYtInfoError(null);
      return;
    }
    setYtInfoLoading(true);
    setYtInfoError(null);
    try {
      const info = await api.getYouTubeVideoInfo(target);
      setYtInfo(info);
      if (info.available_qualities && info.available_qualities.length > 0) {
        setPreferredQuality(prev =>
          info.available_qualities.includes(prev) ? prev : info.available_qualities[0]
        );
      }
    } catch (err: unknown) {
      setYtInfo(null);
      setYtInfoError(getErrorMessage(err, "Không thể lấy thông tin video YouTube."));
    } finally {
      setYtInfoLoading(false);
    }
  }, [youtubeUrl]);

  // Debounced auto-preview whenever a valid YouTube video URL is pasted/entered
  useEffect(() => {
    const trimmed = youtubeUrl.trim();
    if (!trimmed) {
      setYtInfo(null);
      setYtInfoError(null);
      return;
    }
    const vid = getYouTubeId(trimmed);
    if (!vid) {
      setYtInfo(null);
      setYtInfoError(null);
      return;
    }

    let cancelled = false;
    const timer = setTimeout(async () => {
      if (cancelled) return;
      setYtInfoLoading(true);
      setYtInfoError(null);
      try {
        const info = await api.getYouTubeVideoInfo(trimmed);
        if (cancelled) return;
        setYtInfo(info);
        if (info.available_qualities && info.available_qualities.length > 0) {
          setPreferredQuality(prev =>
            info.available_qualities.includes(prev) ? prev : info.available_qualities[0]
          );
        }
      } catch (err: unknown) {
        if (cancelled) return;
        setYtInfo(null);
        setYtInfoError(getErrorMessage(err, "Không thể kiểm tra video YouTube."));
      } finally {
        if (!cancelled) setYtInfoLoading(false);
      }
    }, 450);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [youtubeUrl]);

  const handlePreDownloadYouTube = async () => {
    const trimmed = youtubeUrl.trim();
    if (!trimmed) return;

    if (ytPreDownloadPollRef.current) {
      clearInterval(ytPreDownloadPollRef.current);
      ytPreDownloadPollRef.current = null;
    }
    setError(null);
    setYtDownloading(true);
    setYtDownloadProgress(2);
    setYtDownloadMessage("Đang khởi tạo tải video từ YouTube...");
    setUploadedJobId(null);
    setUploadedVideoUrl(null);
    setUploadedVideoTitle(null);

    try {
      const created = await api.preDownloadYouTubeVideo(trimmed, preferredQuality, targetLanguage);
      setYtDownloadJobId(created.id);
      setYtDownloadProgress(created.progress || 2);
      setYtDownloadMessage(created.message || "Đang tải video từ YouTube...");

      ytPreDownloadPollRef.current = setInterval(async () => {
        try {
          const data = await api.getDubbingJob(created.id);
          setYtDownloadProgress(data.progress || 0);
          setYtDownloadMessage(data.message || "Đang tải video từ YouTube...");

          if (data.status === "uploaded") {
            if (ytPreDownloadPollRef.current) {
              clearInterval(ytPreDownloadPollRef.current);
              ytPreDownloadPollRef.current = null;
            }
            setYtDownloading(false);
            setUploadedJobId(data.id);
            setUploadedVideoUrl(api.getDubbingFileUrl(data.id, "video"));
            setUploadedVideoTitle(data.video_title || ytInfo?.title || "Video YouTube");
            void fetchAllJobs(false);
          } else if (data.status === "failed") {
            if (ytPreDownloadPollRef.current) {
              clearInterval(ytPreDownloadPollRef.current);
              ytPreDownloadPollRef.current = null;
            }
            setYtDownloading(false);
            setError(data.error_message || data.message || "Tải video từ YouTube thất bại.");
          }
        } catch (pollErr: unknown) {
          console.debug("YouTube pre-download poll error:", pollErr);
        }
      }, 1200);
    } catch (err: unknown) {
      setYtDownloading(false);
      setError(getErrorMessage(err, "Không thể bắt đầu tải video YouTube."));
    }
  };

  const handleRetryDubbingJob = async (downloadOnly = false) => {
    if (!jobId) return;
    setRetryingJob(true);
    setError(null);
    try {
      const updated = await api.retryDubbingJob(jobId, {
        preferred_quality: retryQuality,
        download_only: downloadOnly,
      });
      setJob(updated);
      setJobLogs("");
      setPollRevision(r => r + 1);
      void fetchAllJobs(false);
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Không thể thử lại tác vụ."));
    } finally {
      setRetryingJob(false);
    }
  };

  const handleTestYouTubeConnection = async () => {
    setYtDiagTesting(true);
    setYtDiagResult(null);
    try {
      const res = await api.testYouTubeConnection();
      setYtDiagResult({ status: res.status, message: res.message });
      if (res.bridge) {
        setBridgeStatus((prev) => ({
          ...(prev || {}),
          ...res.bridge!,
        }));
      }
    } catch (err: unknown) {
      setYtDiagResult({ status: "error", message: getErrorMessage(err, "Lỗi kiểm tra kết nối YouTube") });
    } finally {
      setYtDiagTesting(false);
    }
  };

  // Handle immediate file upload when selected
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] || null;
    if (!file) return;

    if (ytPreDownloadPollRef.current) {
      clearInterval(ytPreDownloadPollRef.current);
      ytPreDownloadPollRef.current = null;
    }
    setYtDownloading(false);
    setYtDownloadJobId(null);
    setSelectedFile(file);
    setUploading(true);
    setUploadProgress(0);
    setError(null);
    setUploadedJobId(null);
    setUploadedVideoUrl(null);
    setUploadedVideoTitle(null);

    try {
      const response = await api.uploadDubbingVideo(file, (progress) => {
        setUploadProgress(progress);
      });
      setUploadedJobId(response.id);
      setUploadedVideoUrl(api.getDubbingFileUrl(response.id, "video"));
      setUploadedVideoTitle(response.video_title || file.name);
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Tải video lên thất bại. Vui lòng thử lại."));
      setSelectedFile(null);
    } finally {
      setUploading(false);
    }
  };

  const handleStartDubbing = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      if (!selectedFile && !youtubeUrl.trim() && !uploadedJobId) {
        throw new Error("Vui lòng tải lên tệp video hoặc dán link YouTube.");
      }

      const response = await api.createDubbingJob(
        uploadedJobId ? undefined : (selectedFile || undefined),
        uploadedJobId ? undefined : (youtubeUrl.trim() || undefined),
        targetLanguage,
        uploadedJobId || undefined,
        selectedLlmProfileId || undefined,
        {
          preferredQuality,
          videoTitle: uploadedJobId ? undefined : ytInfo?.title,
          videoThumbnail: uploadedJobId ? undefined : (ytInfo?.thumbnail_url || undefined),
          videoDuration: uploadedJobId ? undefined : (ytInfo?.duration ?? undefined),
          voiceMode,
          voiceSampleId: voiceMode === "voice_sample" && selectedVoiceSampleId ? selectedVoiceSampleId : undefined,
        }
      );

      if (ytPreDownloadPollRef.current) {
        clearInterval(ytPreDownloadPollRef.current);
        ytPreDownloadPollRef.current = null;
      }

      // Invalidate any stale in-flight list poll & protect the newly created project
      jobsFetchSeqRef.current += 1;
      activeJobFetchIdRef.current = response.id;
      recentlyCreatedJobsRef.current.set(response.id, { job: response, ts: Date.now() });

      // Clear stale state from any previously viewed job
      setSelectedSegId(null);
      setEditingOrigSegId(null);
      setJobLogs("");

      // Add to open tabs (capped at 8) and activate immediately in workspace
      setOpenJobTabs(prev => {
        const next = [response.id, ...prev.filter(id => id !== response.id)].slice(0, 8);
        localStorage.setItem("open_dubbing_job_tabs", JSON.stringify(next));
        return next;
      });
      setJobId(response.id);
      localStorage.setItem("active_dubbing_job_id", response.id);
      applyJobState(response);
      setAllJobs(prev => [response, ...prev.filter(j => j.id !== response.id)]);
      setPollRevision(r => r + 1);
      setViewMode("workspace");

      // Reset create form inputs so user can create next video immediately if they want
      setYoutubeUrl("");
      setSelectedFile(null);
      setUploadedJobId(null);
      setUploadedVideoUrl(null);
      setUploadedVideoTitle(null);
      setYtInfo(null);
      setYtInfoError(null);
      setYtDownloading(false);
      setYtDownloadJobId(null);
      setYtDownloadProgress(0);
      setYtDownloadMessage("");
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Không thể khởi tạo tác vụ lồng tiếng."));
    } finally {
      setLoading(false);
    }
  };

  const handleSaveSubtitles = async () => {
    if (!jobId) return;
    setSavingSubs(true);
    setError(null);
    try {
      await api.updateDubbingSubtitles(jobId, originalSubs, translatedSubs);
      alert("Đã lưu bản dịch phụ đề thành công!");
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Lỗi lưu phụ đề."));
    } finally {
      setSavingSubs(false);
    }
  };

  const handleFinalize = async () => {
    if (!jobId) return;
    setError(null);
    setLoading(true);
    try {
      await api.updateDubbingSubtitles(jobId, originalSubs, translatedSubs);
      await api.finalizeDubbingJob(
        jobId,
        vocalsVolume,
        bgmVolume,
        voiceMode,
        voiceMode === "voice_sample" ? selectedVoiceSampleId : ""
      );
      const data = await api.getDubbingJob(jobId);
      applyJobState(data);
    } catch (err: unknown) {
      setError(getErrorMessage(err, "Lỗi hoàn tất lồng tiếng."));
    } finally {
      setLoading(false);
    }
  };

  const handleReEditJob = async () => {
    if (!jobId) return;
    setReEditing(true);
    try {
      const updated = await api.reEditDubbingJob(jobId);
      setJob(updated);
      if (updated.translated_subtitles && updated.translated_subtitles.length > 0) {
        setTranslatedSubs(updated.translated_subtitles);
      }
      if (updated.original_subtitles && updated.original_subtitles.length > 0) {
        setOriginalSubs(updated.original_subtitles);
      }
      void fetchAllJobs();
    } catch (e: unknown) {
      alert(`Lỗi khi mở lại chế độ chỉnh sửa: ${getErrorMessage(e, "Vui lòng thử lại")}`);
    } finally {
      setReEditing(false);
    }
  };

  const handleShortenSubtitle = async (segId: number, text: string, targetDur: number, origText?: string) => {
    if (!jobId || !text.trim()) return;
    setShorteningSegId(segId);
    try {
      const res = await api.shortenSubtitle(jobId, {
        segment_id: segId,
        text,
        target_duration: targetDur,
        target_language: targetLanguage || job?.target_language || "Vietnamese",
        original_text: origText,
        llm_profile_id: selectedLlmProfileId || undefined,
      });
      if (res.shortened_text) {
        updateSubText(segId, res.shortened_text);
      }
    } catch (e: unknown) {
      alert(`Không thể rút gọn câu bằng AI: ${getErrorMessage(e, "Mô hình LLM không phản hồi")}`);
    } finally {
      setShorteningSegId(null);
    }
  };

  const handleFastRemix = async () => {
    if (!jobId) return;
    setRemixing(true);
    try {
      await api.remixDubbingAudio(jobId, {
        vocals_volume: remixVocalVol,
        bgm_volume: remixBgmVol,
      });
      setOutputVideoTimestamp(Date.now());
      void fetchAllJobs();
      alert("Đã hòa âm lại và cập nhật video thành công!");
    } catch (e: unknown) {
      alert(`Lỗi hòa âm video: ${getErrorMessage(e, "Không tìm thấy file âm thanh")}`);
    } finally {
      setRemixing(false);
    }
  };

  const jumpToSegment = (start: number, id: number) => {
    setSelectedSegId(id);
    setCurrentVideoTime(start);
    if (videoPlayerRef.current) {
      videoPlayerRef.current.currentTime = start;
      videoPlayerRef.current.play();
    }
  };

  const renderVoiceSelector = (compact = false) => {
    const selectValue =
      voiceMode === "clone_original"
        ? "clone_original"
        : voiceMode === "voice_sample" && selectedVoiceSampleId
          ? `sample:${selectedVoiceSampleId}`
          : "auto_native";

    const previewTargetId =
      voiceMode === "voice_sample" && selectedVoiceSampleId
        ? selectedVoiceSampleId
        : voiceMode === "auto_native" && voiceSamples.length > 0
          ? (voiceSamples.find(v => v.id === "tuan_anh")?.id || voiceSamples[0].id)
          : "";

    return (
      <div className={`flex flex-col gap-2 ${compact ? "p-3" : "p-3.5"} bg-secondary/30 rounded-xl border border-border`}>
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <Mic className="w-3.5 h-3.5 text-primary" />
            <span className="text-xs font-bold text-foreground">Giọng Đọc Lồng Tiếng</span>
            {voiceMode !== "clone_original" ? (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-bold">
                Chuẩn Bản Ngữ
              </span>
            ) : (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-600 dark:text-amber-400 font-bold">
                Clone Ngoại Quốc
              </span>
            )}
          </div>
          {previewTargetId && (
            <button
              type="button"
              onClick={() => toggleVoicePreview(previewTargetId)}
              className={`px-2.5 py-1 rounded-lg text-[10px] font-bold flex items-center gap-1 transition-colors cursor-pointer border ${
                previewingVoiceId === previewTargetId
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-background hover:bg-secondary text-foreground border-border"
              }`}
              title="Nghe thử mẫu giọng đọc"
            >
              {previewingVoiceId === previewTargetId ? (
                <>
                  <VolumeX className="w-3 h-3" />
                  <span>Dừng nghe</span>
                </>
              ) : (
                <>
                  <Play className="w-3 h-3" />
                  <span>Nghe mẫu</span>
                </>
              )}
            </button>
          )}
        </div>

        <select
          value={selectValue}
          onChange={(e) => handleVoiceSelectChange(e.target.value)}
          className="w-full px-3 py-2 text-xs bg-background border border-border rounded-xl text-foreground font-medium focus:outline-none focus:ring-2 focus:ring-primary/30 cursor-pointer"
        >
          <option value="auto_native">
            ✨ Tự động Giọng Việt Bản Ngữ (Khớp Nam/Nữ — Không bị lơ lớ)
          </option>
          {voiceSamples.length > 0 && (
            <optgroup label="Kho Giọng Studio Bản Ngữ">
              {voiceSamples.map((s) => (
                <option key={s.id} value={`sample:${s.id}`}>
                  🎙️ {s.name || s.id}{s.duration ? ` (${s.duration.toFixed(1)}s)` : ""}
                </option>
              ))}
            </optgroup>
          )}
          <optgroup label="Clone Giọng Gốc Video">
            <option value="clone_original">
              🌍 Clone Giọng Gốc Diễn Giả (Giữ màu giọng ngoại quốc — dễ lơ lớ)
            </option>
          </optgroup>
        </select>

        <span className="text-[10px] text-muted-foreground leading-relaxed">
          {voiceMode === "clone_original"
            ? "Lưu ý: Clone trực tiếp từ diễn giả tiếng Anh/ngoại quốc sẽ kéo theo ngữ điệu ngoại quốc khiến câu tiếng Việt hơi lơ lớ."
            : voiceMode === "voice_sample"
              ? "Sử dụng trực tiếp mẫu giọng Studio đã chọn để đọc tiếng Việt tròn vành rõ chữ 100%."
              : "Tự động phân tích cao độ giọng gốc (Nam/Nữ) và chọn giọng Studio tiếng Việt tương ứng, loại bỏ hoàn toàn giọng Tây lơ lớ."}
        </span>
      </div>
    );
  };

  const updateSubText = (id: number, text: string) => {
    setTranslatedSubs(prev =>
      prev.map(item => (item.id === id ? { ...item, text } : item))
    );
  };

  const updateOrigSubText = (id: number, text: string) => {
    setOriginalSubs(prev =>
      prev.map(item => (item.id === id ? { ...item, text } : item))
    );
  };

  const handleDeleteSegment = (segId: number) => {
    setOriginalSubs(prev =>
      prev
        .filter(item => item.id !== segId)
        .map((item, idx) => ({ ...item, id: idx + 1 }))
    );
    setTranslatedSubs(prev =>
      prev
        .filter(item => item.id !== segId)
        .map((item, idx) => ({ ...item, id: idx + 1 }))
    );
    if (selectedSegId === segId) setSelectedSegId(null);
    if (editingOrigSegId === segId) setEditingOrigSegId(null);
  };

  const handleCleanAsrHallucinations = () => {
    const hasScriptCorruption = (s: string) => {
      const hasLatin = /[A-Za-z]/.test(s);
      const hasStray = /[\u0370-\u03ff\u0400-\u04ff]/.test(s);
      return hasLatin && hasStray;
    };
    const cleanSpacingAndPunct = (s: string) =>
      s
        .replace(/(\d)\s+\.(\d)/g, "$1.$2")
        .replace(/([A-Za-z0-9])\s+-([A-Za-z])/g, "$1-$2")
        .replace(/([A-Z]{2,})\s+-(\d)/g, "$1-$2")
        .replace(/\s+([,.;?!:])/g, "$1")
        .replace(/[,;]+\s*\./g, ".")
        .replace(/\s{2,}/g, " ")
        .trim();
    const stripBadSentences = (s: string) => {
      const normalized = cleanSpacingAndPunct(s);
      const parts = normalized.split(/(?<=[.!?])\s+/);
      if (parts.length <= 1) return hasScriptCorruption(normalized) ? "" : normalized;
      return parts.filter(p => p.trim() && !hasScriptCorruption(p)).join(" ").trim();
    };

    const nextOrig: SubtitleSegment[] = [];
    const nextTrans: SubtitleSegment[] = [];

    originalSubs.forEach((orig, idx) => {
      const trans = translatedSubs[idx] || orig;
      const cleanedOrigTxt = stripBadSentences(orig.text || "");
      if (!cleanedOrigTxt) return;

      if (nextOrig.length > 0) {
        const prev = nextOrig[nextOrig.length - 1];
        if (orig.start < prev.end - 0.1 && orig.end <= prev.end + 0.8) {
          return;
        }
      }

      const newId = nextOrig.length + 1;
      const st = nextOrig.length > 0 ? Math.max(nextOrig[nextOrig.length - 1].end, orig.start) : orig.start;
      const et = Math.max(st + 0.4, orig.end);
      const cleanedTransTxt = cleanSpacingAndPunct(trans.text || "");
      nextOrig.push({ ...orig, id: newId, start: st, end: et, text: cleanedOrigTxt });
      nextTrans.push({ ...trans, id: newId, start: st, end: et, text: cleanedTransTxt });
    });

    setOriginalSubs(nextOrig);
    setTranslatedSubs(nextTrans);
  };

  const resetState = () => {
    if (ytPreDownloadPollRef.current) {
      clearInterval(ytPreDownloadPollRef.current);
      ytPreDownloadPollRef.current = null;
    }
    localStorage.removeItem("active_dubbing_job_id");
    setJobId(null);
    setJob(null);
    setYoutubeUrl("");
    setSelectedFile(null);
    setOriginalSubs([]);
    setTranslatedSubs([]);
    setSelectedSegId(null);
    setError(null);
    setUploading(false);
    setUploadProgress(0);
    setUploadedJobId(null);
    setUploadedVideoUrl(null);
    setUploadedVideoTitle(null);
    setYtInfo(null);
    setYtInfoError(null);
    setYtDownloading(false);
    setYtDownloadJobId(null);
    setYtDownloadProgress(0);
    setYtDownloadMessage("");
    setJobLogs("");
    setViewMode("create");
  };

  const handleCloseTab = (tabId: string) => {
    const nextTabs = openJobTabs.filter(id => id !== tabId);
    setOpenJobTabs(nextTabs);
    localStorage.setItem("open_dubbing_job_tabs", JSON.stringify(nextTabs));

    if (jobId === tabId) {
      if (nextTabs.length > 0) {
        const nextId = nextTabs[0];
        openProjectInWorkspace(nextId);
      } else {
        resetState();
      }
    }
  };

  const handleDeleteJob = async (idToDelete: string) => {
    if (!window.confirm("Bạn có chắc chắn muốn xóa vĩnh viễn dự án lồng tiếng này không?")) return;
    try {
      jobsFetchSeqRef.current += 1;
      recentlyDeletedIdsRef.current.set(idToDelete, Date.now());
      recentlyCreatedJobsRef.current.delete(idToDelete);
      await api.deleteDubbingJob(idToDelete);
      setAllJobs(prev => prev.filter(j => j.id !== idToDelete));
      setSelectedJobIds(prev => prev.filter(id => id !== idToDelete));
      handleCloseTab(idToDelete);
      if (jobId === idToDelete) {
        resetState();
      }
    } catch (err: unknown) {
      recentlyDeletedIdsRef.current.delete(idToDelete);
      alert(`Lỗi khi xóa: ${getErrorMessage(err, "Không thể xóa dự án")}`);
    }
  };

  const handleBulkDeleteJobs = async () => {
    if (selectedJobIds.length === 0 || bulkDeleting) return;
    if (!window.confirm(`Bạn có chắc chắn muốn xóa vĩnh viễn ${selectedJobIds.length} dự án đã chọn không?`)) return;
    setBulkDeleting(true);
    const targets = [...selectedJobIds];
    try {
      jobsFetchSeqRef.current += 1;
      const deletedOk: string[] = [];
      for (const tid of targets) {
        try {
          recentlyDeletedIdsRef.current.set(tid, Date.now());
          recentlyCreatedJobsRef.current.delete(tid);
          await api.deleteDubbingJob(tid);
          deletedOk.push(tid);
        } catch (err: unknown) {
          recentlyDeletedIdsRef.current.delete(tid);
          console.error(`Không thể xóa dự án ${tid}:`, err);
        }
      }
      const deletedSet = new Set(deletedOk);
      if (deletedSet.size > 0) {
        setAllJobs(prev => prev.filter(j => !deletedSet.has(j.id)));
        setOpenJobTabs(prev => {
          const next = prev.filter(id => !deletedSet.has(id));
          localStorage.setItem("open_dubbing_job_tabs", JSON.stringify(next));
          return next;
        });
        setSelectedJobIds(prev => prev.filter(id => !deletedSet.has(id)));
        if (jobId && deletedSet.has(jobId)) {
          resetState();
        }
      }
    } finally {
      setBulkDeleting(false);
    }
  };

  // Programmatically ensure video element is 100% muted when separate tracks exist
  useEffect(() => {
    if (videoPlayerRef.current) {
      if (job?.vocals_audio_path || job?.bgm_audio_path) {
        videoPlayerRef.current.muted = true;
        videoPlayerRef.current.volume = 0;
      }
    }
  }, [job?.vocals_audio_path, job?.bgm_audio_path, jobId]);

  // Sync vocals volume and mute
  useEffect(() => {
    if (vocalsPlayerRef.current) {
      vocalsPlayerRef.current.volume = vocalsMuted ? 0 : Math.min(1, Math.max(0, vocalsVolume));
    }
  }, [vocalsVolume, vocalsMuted]);

  // Sync BGM volume and mute
  useEffect(() => {
    if (bgmPlayerRef.current) {
      bgmPlayerRef.current.volume = bgmMuted ? 0 : Math.min(1, Math.max(0, bgmVolume));
    }
  }, [bgmVolume, bgmMuted]);

  // Sync separate audio tracks with video player
  const handleVideoPlay = () => {
    if (job?.vocals_audio_path || job?.bgm_audio_path) {
      if (videoPlayerRef.current) {
        videoPlayerRef.current.muted = true;
        videoPlayerRef.current.volume = 0;
      }
    }
    const t = videoPlayerRef.current?.currentTime || 0;
    setCurrentVideoTime(t);
    if (vocalsPlayerRef.current) {
      vocalsPlayerRef.current.currentTime = t;
      vocalsPlayerRef.current.play().catch(() => {});
    }
    if (bgmPlayerRef.current) {
      bgmPlayerRef.current.currentTime = t;
      bgmPlayerRef.current.play().catch(() => {});
    }
  };

  const handleVideoPause = () => {
    vocalsPlayerRef.current?.pause();
    bgmPlayerRef.current?.pause();
  };

  const handleVideoSeek = () => {
    if (videoPlayerRef.current) {
      const t = videoPlayerRef.current.currentTime;
      setCurrentVideoTime(t);
      if (vocalsPlayerRef.current) vocalsPlayerRef.current.currentTime = t;
      if (bgmPlayerRef.current) bgmPlayerRef.current.currentTime = t;
    }
  };

  const handleVideoRateChange = () => {
    if (videoPlayerRef.current) {
      const rate = videoPlayerRef.current.playbackRate;
      if (vocalsPlayerRef.current) vocalsPlayerRef.current.playbackRate = rate;
      if (bgmPlayerRef.current) bgmPlayerRef.current.playbackRate = rate;
    }
  };

  const handleVideoTimeUpdate = () => {
    if (!videoPlayerRef.current) return;
    const t = videoPlayerRef.current.currentTime;
    setCurrentVideoTime(t);
    if (vocalsPlayerRef.current && Math.abs(vocalsPlayerRef.current.currentTime - t) > 0.2) {
      vocalsPlayerRef.current.currentTime = t;
    }
    if (bgmPlayerRef.current && Math.abs(bgmPlayerRef.current.currentTime - t) > 0.2) {
      bgmPlayerRef.current.currentTime = t;
    }
  };

  const handleVideoWaiting = () => {
    vocalsPlayerRef.current?.pause();
    bgmPlayerRef.current?.pause();
  };

  const handleVideoPlaying = () => {
    const t = videoPlayerRef.current?.currentTime || 0;
    if (vocalsPlayerRef.current && vocalsPlayerRef.current.paused) {
      vocalsPlayerRef.current.currentTime = t;
      vocalsPlayerRef.current.play().catch(() => {});
    }
    if (bgmPlayerRef.current && bgmPlayerRef.current.paused) {
      bgmPlayerRef.current.currentTime = t;
      bgmPlayerRef.current.play().catch(() => {});
    }
  };

  const handleVideoVolumeChange = () => {
    if (job?.vocals_audio_path || job?.bgm_audio_path) {
      if (videoPlayerRef.current && (!videoPlayerRef.current.muted || videoPlayerRef.current.volume > 0)) {
        videoPlayerRef.current.muted = true;
        videoPlayerRef.current.volume = 0;
      }
    }
  };

  // Solo & Mute logic
  const isSoloVocals = !vocalsMuted && bgmMuted;
  const isSoloBgm = vocalsMuted && !bgmMuted;

  const handleToggleSoloVocals = () => {
    if (isSoloVocals) {
      setBgmMuted(false);
    } else {
      setVocalsMuted(false);
      setBgmMuted(true);
    }
  };

  const handleToggleSoloBgm = () => {
    if (isSoloBgm) {
      setVocalsMuted(false);
    } else {
      setVocalsMuted(true);
      setBgmMuted(false);
    }
  };

  const handleResetMix = () => {
    setVocalsVolume(1.0);
    setBgmVolume(0.4);
    setVocalsMuted(false);
    setBgmMuted(false);
  };

  const handleReseparateAudio = async () => {
    if (!jobId) return;
    if (!confirm("Bạn có chắc chắn muốn tách lại âm thanh (Vocals & BGM) bằng bộ tách mới không?")) return;
    try {
      setReseparating(true);
      const updated = await api.reseparateDubbingAudio(jobId);
      setJob(updated);
      setTimeout(() => {
        if (vocalsPlayerRef.current) vocalsPlayerRef.current.load();
        if (bgmPlayerRef.current) bgmPlayerRef.current.load();
      }, 300);
      alert("Tách lại âm thanh thành công! Hãy bấm Play để nghe thử.");
    } catch (err: unknown) {
      alert(`Lỗi khi tách lại âm thanh: ${getErrorMessage(err, "Không thể thực hiện")}`);
    } finally {
      setReseparating(false);
    }
  };

  // Get current step index for the progress stepper
  const getActiveStepIndex = () => {
    if (!job) return 0;
    const statusMap: Record<string, number> = {
      queued: 0,
      downloading: 0,
      separating_audio: 1,
      transcribing: 2,
      translating: 3,
      awaiting_review: 4,
      generating_tts: 5,
      mixing_audio: 6,
      muxing_video: 7,
      completed: 8,
      failed: -1
    };
    return statusMap[job.status] !== undefined ? statusMap[job.status] : 0;
  };

  const currentStepIdx = getActiveStepIndex();

  const activeProcessingJobs = allJobs.filter(
    j => j.status !== "completed" && j.status !== "failed" && j.status !== "uploaded" && j.status !== "awaiting_review"
  );
  const reviewJobs = allJobs.filter(j => j.status === "awaiting_review");
  const completedJobs = allJobs.filter(j => j.status === "completed");
  const uploadedJobs = allJobs.filter(j => j.status === "uploaded");
  const failedJobs = allJobs.filter(j => j.status === "failed");

  const filteredJobs = allJobs
    .filter(j => {
      if (jobsFilter === "running") {
        if (j.status === "completed" || j.status === "failed" || j.status === "uploaded" || j.status === "awaiting_review") return false;
      } else if (jobsFilter === "awaiting_review") {
        if (j.status !== "awaiting_review") return false;
      } else if (jobsFilter === "completed") {
        if (j.status !== "completed") return false;
      } else if (jobsFilter === "uploaded") {
        if (j.status !== "uploaded") return false;
      } else if (jobsFilter === "failed") {
        if (j.status !== "failed") return false;
      }

      if (jobsSearch.trim()) {
        const q = jobsSearch.toLowerCase();
        const matchTitle = j.video_title?.toLowerCase().includes(q);
        const matchUrl = j.source_url?.toLowerCase().includes(q);
        const matchId = (j.id || "").toLowerCase().includes(q);
        const matchLang = (j.target_language || "").toLowerCase().includes(q);
        const matchVoice = (j.voice_sample_id || "").toLowerCase().includes(q);
        return Boolean(matchTitle || matchUrl || matchId || matchLang || matchVoice);
      }
      return true;
    })
    .sort((a, b) => {
      const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
      const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
      if (jobsSort === "oldest") return ta - tb;
      if (jobsSort === "review_first") {
        const ra = a.status === "awaiting_review" ? 0 : 1;
        const rb = b.status === "awaiting_review" ? 0 : 1;
        if (ra !== rb) return ra - rb;
        return tb - ta;
      }
      if (jobsSort === "running_first") {
        const isRun = (st: string) => !["completed", "failed", "uploaded", "awaiting_review"].includes(st);
        const ra = isRun(a.status) ? 0 : a.status === "awaiting_review" ? 1 : 2;
        const rb = isRun(b.status) ? 0 : b.status === "awaiting_review" ? 1 : 2;
        if (ra !== rb) return ra - rb;
        return tb - ta;
      }
      return tb - ta;
    });

  const totalJobPages = Math.max(1, jobsPageSize >= 999 ? 1 : Math.ceil(filteredJobs.length / jobsPageSize));
  const safeJobsPage = Math.min(jobsPage, totalJobPages);
  const paginatedJobs =
    jobsPageSize >= 999
      ? filteredJobs
      : filteredJobs.slice((safeJobsPage - 1) * jobsPageSize, safeJobsPage * jobsPageSize);

  const visibleJobIds = paginatedJobs.map(j => j.id);
  const allVisibleSelected = visibleJobIds.length > 0 && visibleJobIds.every(id => selectedJobIds.includes(id));

  const toggleSelectAllVisible = () => {
    if (allVisibleSelected) {
      const visSet = new Set(visibleJobIds);
      setSelectedJobIds(prev => prev.filter(id => !visSet.has(id)));
    } else {
      setSelectedJobIds(prev => Array.from(new Set([...prev, ...visibleJobIds])));
    }
  };

  const toggleSelectJob = (id: string) => {
    setSelectedJobIds(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]));
  };

  const getStatusMeta = (st: string, progress: number) => {
    const map: Record<string, { label: string; dot: string; badge: string; actionLabel: string }> = {
      queued: {
        label: `Đang xếp hàng (${progress}%)`,
        dot: "bg-primary animate-pulse",
        badge: "bg-primary/10 text-primary border-primary/25",
        actionLabel: "Xem tiến độ",
      },
      downloading: {
        label: `Đang tải video (${progress}%)`,
        dot: "bg-primary animate-pulse",
        badge: "bg-primary/10 text-primary border-primary/25",
        actionLabel: "Xem tiến độ",
      },
      separating_audio: {
        label: `Tách nhạc nền (${progress}%)`,
        dot: "bg-primary animate-pulse",
        badge: "bg-primary/10 text-primary border-primary/25",
        actionLabel: "Xem tiến độ",
      },
      transcribing: {
        label: `Nhận dạng ASR (${progress}%)`,
        dot: "bg-primary animate-pulse",
        badge: "bg-primary/10 text-primary border-primary/25",
        actionLabel: "Xem tiến độ",
      },
      translating: {
        label: `Đang dịch AI (${progress}%)`,
        dot: "bg-primary animate-pulse",
        badge: "bg-primary/10 text-primary border-primary/25",
        actionLabel: "Xem tiến độ",
      },
      awaiting_review: {
        label: "Chờ duyệt phụ đề",
        dot: "bg-amber-500",
        badge: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30",
        actionLabel: "Duyệt phụ đề",
      },
      generating_tts: {
        label: `Sinh giọng đọc (${progress}%)`,
        dot: "bg-indigo-500 animate-pulse",
        badge: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/30",
        actionLabel: "Xem tiến độ",
      },
      mixing_audio: {
        label: `Trộn âm thanh (${progress}%)`,
        dot: "bg-indigo-500 animate-pulse",
        badge: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/30",
        actionLabel: "Xem tiến độ",
      },
      muxing_video: {
        label: `Đóng gói MP4 (${progress}%)`,
        dot: "bg-indigo-500 animate-pulse",
        badge: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/30",
        actionLabel: "Xem tiến độ",
      },
      completed: {
        label: "Hoàn tất",
        dot: "bg-emerald-500",
        badge: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30",
        actionLabel: "Mở kết quả",
      },
      uploaded: {
        label: "Đã tải sẵn",
        dot: "bg-sky-500",
        badge: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/30",
        actionLabel: "Mở Studio",
      },
      failed: {
        label: "Thất bại",
        dot: "bg-destructive",
        badge: "bg-destructive/10 text-destructive border-destructive/30",
        actionLabel: "Xem / Thử lại",
      },
    };
    return map[st] || {
      label: `${st} (${progress}%)`,
      dot: "bg-primary",
      badge: "bg-primary/10 text-primary border-primary/25",
      actionLabel: "Mở Studio",
    };
  };

  return (
    <div className="w-full flex flex-col gap-6 animate-fadeIn">
      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <PageHeader
          title="Studio Lồng Tiếng Video AI"
          description="Lồng tiếng video đa ngôn ngữ song song, bóc tách nhạc nền Demucs, dịch thuật AI & clone giọng OmniVoice."
          icon={<Film className="w-5 h-5" />}
        />
        
        <div className="flex items-center gap-2">
          {/* Button: YouTube / Proxy / Tunnel Bridge Settings (Admin only) */}
          {isAdmin && (
            <button
              type="button"
              onClick={() => {
                setShowCookieModal(true);
                void fetchBridgeStatus();
              }}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-xl border transition-all cursor-pointer ${
                bridgeStatus?.bridge_online
                  ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/20"
                  : proxyPool.length > 0 
                  ? "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/30 hover:bg-purple-500/20"
                  : oauthStatus?.connected
                  ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/20"
                  : "bg-secondary hover:bg-secondary/80 text-foreground border-border"
              }`}
            >
              <Globe className="w-3.5 h-3.5" />
              <span>
                {bridgeStatus?.bridge_online
                  ? "Bridge: Online"
                  : proxyPool.length > 0
                  ? `Proxy Pool (${proxyPool.length})`
                  : oauthStatus?.connected
                  ? "YouTube: Đã kết nối"
                  : "Proxy / YouTube"}
              </span>
            </button>
          )}

          {/* Button: Tất Cả Dự Án */}
          <button
            type="button"
            onClick={() => {
              setViewMode("list");
              fetchAllJobs(true);
            }}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-xl border transition-all cursor-pointer ${
              viewMode === "list"
                ? "bg-primary text-primary-foreground border-primary shadow-sm"
                : "bg-secondary hover:bg-secondary/80 text-foreground border-border"
            }`}
          >
            <Layers className="w-3.5 h-3.5" />
            <span>Quản Lý Dự Án</span>
            {allJobs.length > 0 && (
              <span className={`px-1.5 py-0.2 text-[10px] font-bold rounded-full ${
                viewMode === "list" ? "bg-white/20 text-white" : "bg-primary/20 text-primary"
              }`}>
                {allJobs.length}
              </span>
            )}
          </button>

          {/* Button: Tạo Dự Án Mới */}
          <button
            type="button"
            onClick={() => setViewMode("create")}
            className={`flex items-center gap-1.5 px-3.5 py-2 text-xs font-bold rounded-xl border transition-all cursor-pointer ${
              viewMode === "create"
                ? "bg-gradient-to-r from-primary to-accent text-white border-transparent shadow-md"
                : "bg-primary/10 text-primary border-primary/20 hover:bg-primary/20"
            }`}
          >
            <Plus className="w-3.5 h-3.5" />
            <span>Dự Án Mới</span>
          </button>
        </div>
      </div>

      {/* --- REALTIME PROJECT SWITCHER TAB BAR --- */}
      <div className="flex items-center gap-2 overflow-x-auto pb-1 border-b border-border/60 scrollbar-thin">
        {/* Tab 1: Tạo Dự Án Mới */}
        <button
          type="button"
          onClick={() => setViewMode("create")}
          className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold whitespace-nowrap transition-all cursor-pointer shrink-0 ${
            viewMode === "create"
              ? "bg-primary text-primary-foreground shadow-sm"
              : "bg-secondary/50 text-muted-foreground hover:text-foreground hover:bg-secondary"
          }`}
        >
          <Plus className="w-3.5 h-3.5" />
          <span>+ Dự Án Mới</span>
        </button>

        {/* Tab pills for open jobs */}
        {openJobTabs.map((tid) => {
          const tabJob = allJobs.find(j => j.id === tid) || (tid === jobId ? job : null);
          const isSelected = viewMode === "workspace" && jobId === tid;
          
          const isReview = tabJob?.status === "awaiting_review";
          const isCompleted = tabJob?.status === "completed";
          const isFailed = tabJob?.status === "failed";
          const isUploaded = tabJob?.status === "uploaded";
          const isRunning = tabJob && !isCompleted && !isFailed && !isReview && !isUploaded;

          const title = tabJob?.video_title
            ? tabJob.video_title
            : tabJob?.source_url
            ? (tabJob.source_url.includes("youtu") ? "YouTube: " + (getYouTubeId(tabJob.source_url) || tabJob.source_url) : tabJob.source_url.split("/").pop())
            : `Dự án #${tid.slice(0, 6)}`;

          return (
            <div
              key={tid}
              className={`group flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-all cursor-pointer shrink-0 border ${
                isSelected
                  ? "bg-card text-foreground border-primary shadow-sm ring-1 ring-primary/30 font-bold"
                  : isReview
                  ? "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30 hover:bg-amber-500/20"
                  : "bg-secondary/30 text-muted-foreground border-border/50 hover:text-foreground hover:bg-secondary"
              }`}
              onClick={() => openProjectInWorkspace(tid, tabJob)}
            >
              {isRunning && <Loader2 className="w-3 h-3 animate-spin text-primary shrink-0" />}
              {isReview && <span className="inline-block w-2 h-2 rounded-full bg-amber-500 shrink-0" />}
              {isCompleted && <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0" />}
              {isUploaded && <Film className="w-3 h-3 text-blue-500 shrink-0" />}
              {isFailed && <AlertCircle className="w-3 h-3 text-destructive shrink-0" />}
              
              <span className="max-w-[130px] truncate">{title}</span>
              
              {tabJob && (
                <span className={`text-[10px] px-1.5 py-0.2 rounded-full font-bold ${
                  isReview 
                    ? "bg-amber-500/20 text-amber-600 dark:text-amber-400" 
                    : isCompleted 
                    ? "bg-emerald-500/20 text-emerald-600 dark:text-emerald-400" 
                    : isUploaded
                    ? "bg-blue-500/20 text-blue-600 dark:text-blue-400"
                    : isFailed 
                    ? "bg-destructive/20 text-destructive"
                    : "bg-primary/20 text-primary"
                }`}>
                  {isReview ? "Chờ duyệt" : isCompleted ? "Xong" : isUploaded ? "Sẵn sàng" : isFailed ? "Lỗi" : `${tabJob.progress}%`}
                </span>
              )}

              {/* Close tab button */}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleCloseTab(tid);
                }}
                className="opacity-50 hover:opacity-100 p-0.5 rounded hover:bg-secondary transition-opacity ml-0.5"
                title="Đóng tab này"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          );
        })}

        {allJobs.length > openJobTabs.length && (
          <button
            type="button"
            onClick={() => {
              setViewMode("list");
              fetchAllJobs(true);
            }}
            className="text-[11px] text-muted-foreground hover:text-primary font-medium px-2 py-1 shrink-0 ml-auto cursor-pointer"
          >
            Xem tất cả ({allJobs.length}) &rarr;
          </button>
        )}
      </div>

      {error && (
        <div className="p-4 bg-destructive/10 border border-destructive/30 text-destructive rounded-xl text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* --- VIEW MODE 2: WORKSPACE (Tiến trình realtime) --- */}
      {viewMode === "workspace" && jobId && job && job.id === jobId && (
        <>
          {/* Workspace Job Top Bar */}
          <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 p-4 bg-card border border-border rounded-2xl shadow-sm">
            <div className="flex items-center gap-3">
              <div className={`p-2.5 rounded-xl shrink-0 ${
                job.source_type === "youtube" ? "bg-red-500/10 text-red-500" : "bg-primary/10 text-primary"
              }`}>
                {job.source_type === "youtube" ? <Play className="w-5 h-5" /> : <Film className="w-5 h-5" />}
              </div>
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-mono font-bold text-muted-foreground">ID: #{job.id.slice(0, 8)}</span>
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                    job.status === "completed"
                      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30"
                      : job.status === "awaiting_review"
                      ? "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30 animate-pulse"
                      : job.status === "uploaded"
                      ? "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/30"
                      : job.status === "failed"
                      ? "bg-destructive/10 text-destructive border-destructive/30"
                      : "bg-primary/10 text-primary border-primary/30"
                  }`}>
                    {job.status === "completed" ? "Hoàn tất" : job.status === "awaiting_review" ? "Cần kiểm duyệt" : job.status === "uploaded" ? "Đã tải sẵn" : job.status === "failed" ? "Lỗi" : `Đang xử lý ${job.progress}%`}
                  </span>
                  {job.preferred_quality && job.source_type === "youtube" && (
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-secondary text-muted-foreground border border-border">
                      {job.preferred_quality}
                    </span>
                  )}
                </div>
                <h3 className="text-sm font-bold text-foreground line-clamp-1 max-w-md sm:max-w-xl mt-0.5">
                  {job.video_title || job.source_url || `Dự án video #${job.id.slice(0, 8)}`}
                </h3>
                {job.source_url && job.video_title && (
                  <a
                    href={job.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[10px] text-muted-foreground hover:text-primary mt-0.5 truncate max-w-md"
                  >
                    <span className="truncate">{job.source_url}</span>
                    <ExternalLink className="w-2.5 h-2.5 shrink-0" />
                  </a>
                )}
              </div>
            </div>

            <div className="flex items-center gap-2 w-full sm:w-auto justify-end flex-wrap">
              {job.status === "completed" && (
                <a
                  href={api.getDubbingFileUrl(job.id, "output", true)}
                  download={`dubbed_${job.id}.mp4`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3.5 py-1.5 bg-gradient-to-r from-primary to-accent hover:brightness-105 text-white text-xs font-bold rounded-xl shadow transition-all flex items-center gap-1.5 cursor-pointer"
                  title="Tải video MP4 đã lồng tiếng về máy"
                >
                  <Download className="w-3.5 h-3.5" />
                  <span>Tải Video Lồng Tiếng (MP4)</span>
                </a>
              )}

              {job.has_source_video && (
                <a
                  href={api.getDubbingFileUrl(job.id, "video", true)}
                  download={`youtube_${job.id}.mp4`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 text-xs font-bold rounded-xl border border-emerald-500/30 transition-colors flex items-center gap-1.5 cursor-pointer"
                  title="Tải tệp MP4 gốc đã tải về máy"
                >
                  <Download className="w-3.5 h-3.5" />
                  <span>Tải Video Gốc (MP4)</span>
                </a>
              )}

              <button
                type="button"
                onClick={() => setViewMode("create")}
                className="px-3 py-1.5 bg-primary/10 hover:bg-primary/20 text-primary text-xs font-bold rounded-xl border border-primary/20 transition-all flex items-center gap-1.5 cursor-pointer"
                title="Tạo thêm video khác"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>+ Thêm Video</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  setViewMode("list");
                  fetchAllJobs(true);
                }}
                className="px-3 py-1.5 bg-secondary hover:bg-secondary/80 text-foreground text-xs font-semibold rounded-xl border border-border transition-colors flex items-center gap-1.5 cursor-pointer"
              >
                <Layers className="w-3.5 h-3.5" />
                <span>Tất Cả Dự Án</span>
              </button>

              <button
                type="button"
                onClick={() => handleDeleteJob(job.id)}
                className="p-1.5 hover:bg-destructive/10 text-muted-foreground hover:text-destructive rounded-xl border border-border/60 transition-colors cursor-pointer"
                title="Xóa dự án này"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>

          <div className={job.status === "completed" || job.status === "awaiting_review" ? "order-last" : ""}>
          <SectionCard title="Tiến Trình Xử Lý Realtime">
          <div className="flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-primary animate-pulse" />
                <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                  {job.status.toUpperCase()}
                </span>
              </div>
              <span className="text-xs font-bold text-primary">
                {job.progress}%
              </span>
            </div>

            {/* Stepper bubbles */}
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2 pt-2">
              {PIPELINE_STEPS.map((step, idx) => {
                const IconComponent = step.icon;
                const isDone = currentStepIdx > idx || job.status === "completed";
                const isCurrent = currentStepIdx === idx && job.status !== "completed" && job.status !== "failed";
                const isFailed = job.status === "failed" && currentStepIdx === idx;

                return (
                  <div
                    key={step.id}
                    className={`flex flex-col items-center p-2.5 rounded-xl border transition-all text-center relative ${
                      isDone
                        ? "bg-primary/5 border-primary/30 text-foreground"
                        : isCurrent
                        ? "bg-primary/15 border-primary text-primary font-bold shadow-sm ring-2 ring-primary/20"
                        : isFailed
                        ? "bg-destructive/10 border-destructive text-destructive"
                        : "bg-secondary/20 border-border/50 text-muted-foreground opacity-60"
                    }`}
                  >
                    <div className="mb-1.5 relative">
                      {isDone ? (
                        <CheckCircle2 className="w-5 h-5 text-primary" />
                      ) : isCurrent ? (
                        <Loader2 className="w-5 h-5 text-primary animate-spin" />
                      ) : (
                        <IconComponent className="w-5 h-5" />
                      )}
                    </div>
                    <span className="text-[11px] leading-tight font-bold line-clamp-1">{step.label}</span>
                    <span className="text-[9px] text-muted-foreground mt-0.5 line-clamp-1 hidden sm:block">{step.desc}</span>
                  </div>
                );
              })}
            </div>

            {/* Live Message */}
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 p-3 bg-secondary/40 border border-border/60 rounded-xl text-xs text-foreground">
              <div className="flex items-center gap-2">
                {job.status !== "completed" && job.status !== "failed" && job.status !== "uploaded" ? (
                  <Clock className="w-3.5 h-3.5 text-muted-foreground shrink-0 animate-spin" />
                ) : (
                  <Clock className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                )}
                <span className="font-medium">{job.message || "Đang xử lý tiến trình..."}</span>
              </div>
              {job.status === "translating" && (
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      setRetranslating(true);
                      const updated = await api.retryDubbingTranslation(job.id, { force_fallback: true });
                      setJob(updated);
                      if (updated.original_subtitles) setOriginalSubs(updated.original_subtitles);
                      if (updated.translated_subtitles) setTranslatedSubs(updated.translated_subtitles);
                    } catch (e: unknown) {
                      alert(getErrorMessage(e, "Không thể bỏ qua"));
                    } finally {
                      setRetranslating(false);
                    }
                  }}
                  disabled={retranslating}
                  className="px-2.5 py-1 bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30 rounded-lg font-bold text-[11px] flex items-center gap-1 cursor-pointer transition-colors"
                  title="Bỏ qua dịch thuật LLM nếu bị kẹt và chuyển ngay sang bước duyệt phụ đề"
                >
                  <Zap className="w-3.5 h-3.5" />
                  <span>{retranslating ? "Đang mở duyệt..." : "Bỏ Qua Dịch & Mở Duyệt Ngay"}</span>
                </button>
              )}
            </div>

            {/* Uploaded Draft Preview & Start Dubbing Card */}
            {job.status === "uploaded" && (
              <div className="p-4 bg-blue-500/5 border border-blue-500/25 rounded-2xl flex flex-col gap-4">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div>
                    <h4 className="text-xs font-bold text-foreground flex items-center gap-1.5">
                      <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                      <span>Video Gốc Đã Sẵn Sàng Trên Máy Chủ</span>
                    </h4>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      Bạn có thể xem trước, tải MP4 về máy hoặc bắt đầu quy trình nhận dạng &amp; dịch thuật ngay.
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {job.has_source_video && (
                      <a
                        href={api.getDubbingFileUrl(job.id, "video", true)}
                        download={`youtube_${job.id}.mp4`}
                        className="px-3 py-1.5 bg-secondary hover:bg-secondary/80 text-foreground text-xs font-bold rounded-xl border border-border transition-colors flex items-center gap-1.5"
                      >
                        <Download className="w-3.5 h-3.5" />
                        <span>Tải MP4 Về Máy</span>
                      </a>
                    )}
                    <button
                      type="button"
                      disabled={loading}
                      onClick={async () => {
                        setLoading(true);
                        setError(null);
                        try {
                          const started = await api.createDubbingJob(
                            undefined,
                            undefined,
                            targetLanguage || job.target_language || "Vietnamese",
                            job.id,
                            selectedLlmProfileId || undefined,
                            { preferredQuality: job.preferred_quality || preferredQuality }
                          );
                          setJob(started);
                          setPollRevision(r => r + 1);
                          void fetchAllJobs(false);
                        } catch (e: unknown) {
                          setError(getErrorMessage(e, "Không thể bắt đầu quy trình lồng tiếng."));
                        } finally {
                          setLoading(false);
                        }
                      }}
                      className="px-4 py-1.5 bg-gradient-to-r from-primary to-accent hover:brightness-105 text-white text-xs font-bold rounded-xl shadow transition-all flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
                    >
                      {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                      <span>Bắt Đầu Nhận Dạng &amp; Dịch Thuật</span>
                    </button>
                  </div>
                </div>
                {job.has_source_video && (
                  <div className="aspect-video w-full max-w-2xl mx-auto rounded-xl overflow-hidden bg-black border border-border">
                    <video
                      src={api.getDubbingFileUrl(job.id, "video")}
                      controls
                      className="w-full h-full object-contain"
                    />
                  </div>
                )}
              </div>
            )}

            {/* Failed Job Error & Retry Card */}
            {job.status === "failed" && (
              <div className="p-4 bg-destructive/10 border border-destructive/30 rounded-2xl flex flex-col gap-3">
                <div className="flex items-start gap-2.5">
                  <AlertCircle className="w-4 h-4 text-destructive shrink-0 mt-0.5" />
                  <div className="flex flex-col gap-1 min-w-0">
                    <h4 className="text-xs font-bold text-destructive">Xử Lý Thất Bại — Chi Tiết Lỗi</h4>
                    <pre className="text-[11px] font-mono text-destructive/90 whitespace-pre-wrap break-words bg-background/60 p-2.5 rounded-xl border border-destructive/20">
                      {job.error_message || job.message || "Không xác định được lỗi."}
                    </pre>
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                  <div className="flex items-center gap-2">
                    <label className="text-[11px] font-semibold text-foreground">Chất lượng thử lại:</label>
                    <select
                      value={retryQuality}
                      onChange={(e) => setRetryQuality(e.target.value)}
                      className="bg-background border border-border rounded-lg px-2.5 py-1 text-xs font-medium text-foreground focus:outline-none focus:border-primary"
                    >
                      <option value="2160p">4K Ultra HD (2160p)</option>
                      <option value="1440p">2K QHD (1440p)</option>
                      <option value="1080p">1080p Full HD</option>
                      <option value="720p">720p HD</option>
                      <option value="480p">480p SD</option>
                      <option value="360p">360p Nhanh</option>
                    </select>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void handleRetryDubbingJob(false)}
                      disabled={retryingJob}
                      className="px-3.5 py-1.5 bg-primary hover:bg-primary/90 text-primary-foreground text-xs font-bold rounded-xl shadow-sm transition-all flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
                    >
                      {retryingJob ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                      <span>Thử Tải &amp; Chạy Lại</span>
                    </button>
                    {job.source_type === "youtube" && (
                      <button
                        type="button"
                        onClick={() => void handleRetryDubbingJob(true)}
                        disabled={retryingJob}
                        className="px-3 py-1.5 bg-secondary hover:bg-secondary/80 text-foreground text-xs font-bold rounded-xl border border-border transition-colors flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
                      >
                        <Download className="w-3.5 h-3.5" />
                        <span>Chỉ Tải Lại Video MP4</span>
                      </button>
                    )}
                    {isAdmin && (
                      <button
                        type="button"
                        onClick={() => setShowCookieModal(true)}
                        className="px-3 py-1.5 bg-secondary hover:bg-secondary/80 text-foreground text-xs font-semibold rounded-xl border border-border transition-colors flex items-center gap-1.5 cursor-pointer"
                      >
                        <Key className="w-3.5 h-3.5 text-primary" />
                        <span>Cấu Hình Proxy / Cookie YouTube</span>
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Console log terminal */}
            <div className="mt-4 flex flex-col gap-2">
              <div className="flex justify-between items-center">
                <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                  <span className="inline-block w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
                  Console Log Chẩn Đoán & Báo Cáo
                </span>
                <button
                  type="button"
                  onClick={async () => {
                    if (jobId) {
                      try {
                        const logData = await api.getDubbingJobLog(jobId);
                        setJobLogs(logData.log);
                      } catch {
                        // Ignore log refresh error
                      }
                    }
                  }}
                  className="text-[10px] text-primary hover:underline font-semibold flex items-center gap-1 cursor-pointer"
                >
                  <RefreshCw className="w-2.5 h-2.5" /> Làm mới log
                </button>
              </div>
              <div className="bg-[#18181b] border border-border/60 rounded-xl p-4 font-mono text-[11px] text-[#a1a1aa] overflow-y-auto max-h-60 flex flex-col gap-1.5 shadow-inner">
                {jobLogs ? (
                  jobLogs.split("\n").map((line, lIdx) => {
                    if (!line.trim()) return null;
                    const isError = line.includes("LỖI") || line.includes("FAILED") || line.includes("ERROR");
                    const isSuccess = line.includes("thành công") || line.includes("SUCCESS") || line.includes("hoàn tất");
                    const isKaggle = line.includes("[KAGGLE]") || line.includes("[MOCK]");
                    return (
                      <div key={lIdx} className="leading-relaxed break-all text-left">
                        <span className={isError ? "text-red-400 font-bold" : isSuccess ? "text-emerald-400" : isKaggle ? "text-cyan-400" : "text-zinc-300"}>
                          {line}
                        </span>
                      </div>
                    );
                  })
                ) : (
                  <div className="text-zinc-500 italic text-left">Đang tải nhật ký tiến trình...</div>
                )}
                <div ref={logsEndRef} />
              </div>
            </div>
          </div>
        </SectionCard>
          </div>
        </>
      )}

      {/* Workspace Loading State */}
      {viewMode === "workspace" && jobId && (!job || job.id !== jobId) && loadingJobDetails && (
        <div className="p-12 bg-card border border-border rounded-2xl flex flex-col items-center justify-center text-center gap-3 shadow-sm">
          <Loader2 className="w-8 h-8 text-primary animate-spin" />
          <div>
            <h4 className="text-sm font-bold text-foreground">Đang tải nội dung dự án #{jobId.slice(0, 8)}...</h4>
            <p className="text-xs text-muted-foreground mt-1">Đồng bộ phụ đề, âm thanh và trạng thái mới nhất từ máy chủ.</p>
          </div>
        </div>
      )}

      {/* Workspace Empty State */}
      {viewMode === "workspace" && (!jobId || ((!job || job.id !== jobId) && !loadingJobDetails)) && (
        <div className="p-12 bg-secondary/10 border border-dashed border-border rounded-2xl flex flex-col items-center justify-center text-center gap-3">
          <Film className="w-10 h-10 text-muted-foreground/50" />
          <div>
            <h4 className="text-sm font-bold text-foreground">Chưa có dự án nào được chọn</h4>
            <p className="text-xs text-muted-foreground mt-1">Chọn một dự án từ danh sách hoặc khởi tạo video lồng tiếng mới.</p>
          </div>
          <div className="flex items-center gap-2 mt-2">
            <button
              type="button"
              onClick={() => setViewMode("create")}
              className="px-4 py-2 bg-primary hover:bg-primary/90 text-primary-foreground font-bold text-xs rounded-xl shadow transition-all flex items-center gap-1.5 cursor-pointer"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>+ Tạo Dự Án Mới</span>
            </button>
            <button
              type="button"
              onClick={() => {
                setViewMode("list");
                fetchAllJobs(true);
              }}
              className="px-4 py-2 bg-secondary hover:bg-secondary/80 text-foreground font-bold text-xs rounded-xl border border-border transition-colors flex items-center gap-1.5 cursor-pointer"
            >
              <Layers className="w-3.5 h-3.5" />
              <span>Xem Tất Cả Dự Án ({allJobs.length})</span>
            </button>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* VIEW MODE 3: REDESIGNED PROJECT MANAGEMENT TABLE (viewMode === "list")    */}
      {/* ========================================================================= */}
      {viewMode === "list" && (
        <div className="flex flex-col gap-4 animate-fadeIn">
          {/* 1. Compact Clickable KPI Status Strip */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5">
            {[
              { id: "all", label: "Tất cả dự án", count: allJobs.length, tone: "border-primary/40 bg-primary/5 text-primary", dot: "bg-primary" },
              { id: "running", label: "Đang xử lý", count: activeProcessingJobs.length, tone: "border-indigo-500/40 bg-indigo-500/5 text-indigo-600 dark:text-indigo-400", dot: activeProcessingJobs.length > 0 ? "bg-indigo-500 animate-pulse" : "bg-indigo-400" },
              { id: "awaiting_review", label: "Chờ duyệt phụ đề", count: reviewJobs.length, tone: "border-amber-500/40 bg-amber-500/5 text-amber-600 dark:text-amber-400", dot: "bg-amber-500" },
              { id: "completed", label: "Hoàn tất", count: completedJobs.length, tone: "border-emerald-500/40 bg-emerald-500/5 text-emerald-600 dark:text-emerald-400", dot: "bg-emerald-500" },
              { id: "uploaded", label: "Đã tải sẵn", count: uploadedJobs.length, tone: "border-sky-500/40 bg-sky-500/5 text-sky-600 dark:text-sky-400", dot: "bg-sky-500" },
              { id: "failed", label: "Thất bại", count: failedJobs.length, tone: "border-destructive/40 bg-destructive/5 text-destructive", dot: "bg-destructive" },
            ].map((tile) => {
              const active = jobsFilter === tile.id;
              return (
                <button
                  key={tile.id}
                  type="button"
                  onClick={() => {
                    setJobsFilter(tile.id);
                    setJobsPage(1);
                  }}
                  className={`flex flex-col justify-between p-3 rounded-xl border text-left transition-all cursor-pointer ${
                    active
                      ? `${tile.tone} shadow-sm ring-1 ring-current/20`
                      : "bg-card hover:bg-secondary/40 border-border text-foreground"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 w-full">
                    <span className="text-[11px] font-semibold text-muted-foreground truncate">{tile.label}</span>
                    <span className={`w-2 h-2 rounded-full shrink-0 ${tile.dot}`} />
                  </div>
                  <span className="text-xl font-extrabold tracking-tight mt-1 tabular-nums">{tile.count}</span>
                </button>
              );
            })}
          </div>

          {/* 2. Unified Management Toolbar + Bulk Actions */}
          <div className="bg-card border border-border rounded-2xl shadow-sm overflow-hidden">
            <div className="p-3.5 border-b border-border flex flex-col md:flex-row items-stretch md:items-center justify-between gap-3">
              <div className="flex items-center gap-2 flex-wrap flex-1">
                <div className="relative flex-1 min-w-[200px] max-w-md">
                  <Search className="w-3.5 h-3.5 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    placeholder="Tìm tên video, mã #vd_..., link YouTube, ngôn ngữ..."
                    value={jobsSearch}
                    onChange={(e) => {
                      setJobsSearch(e.target.value);
                      setJobsPage(1);
                    }}
                    className="w-full bg-background border border-border rounded-xl pl-9 pr-8 py-2 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-primary"
                  />
                  {jobsSearch && (
                    <button
                      type="button"
                      onClick={() => {
                        setJobsSearch("");
                        setJobsPage(1);
                      }}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground cursor-pointer"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>

                <div className="flex items-center gap-1.5 bg-background border border-border rounded-xl px-2.5 py-1.5">
                  <ArrowUpDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                  <select
                    value={jobsSort}
                    onChange={(e) => {
                      setJobsSort(e.target.value as "newest" | "oldest" | "review_first" | "running_first");
                      setJobsPage(1);
                    }}
                    className="bg-transparent text-xs font-medium text-foreground focus:outline-none cursor-pointer"
                  >
                    <option value="newest">Mới nhất trước</option>
                    <option value="oldest">Cũ nhất trước</option>
                    <option value="running_first">Ưu tiên đang chạy</option>
                    <option value="review_first">Ưu tiên chờ duyệt</option>
                  </select>
                </div>
              </div>

              <div className="flex items-center gap-2 justify-end flex-wrap">
                {selectedJobIds.length > 0 && (
                  <div className="flex items-center gap-2 px-2.5 py-1 bg-destructive/10 border border-destructive/30 rounded-xl">
                    <span className="text-[11px] font-bold text-destructive">
                      Đã chọn {selectedJobIds.length}
                    </span>
                    <button
                      type="button"
                      onClick={() => setSelectedJobIds([])}
                      className="text-[11px] text-muted-foreground hover:text-foreground underline cursor-pointer"
                    >
                      Bỏ chọn
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleBulkDeleteJobs()}
                      disabled={bulkDeleting}
                      className="px-2.5 py-1 bg-destructive hover:bg-destructive/90 text-white text-[11px] font-bold rounded-lg flex items-center gap-1 disabled:opacity-50 cursor-pointer"
                    >
                      {bulkDeleting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                      <span>Xóa đã chọn</span>
                    </button>
                  </div>
                )}

                <button
                  type="button"
                  onClick={() => fetchAllJobs(true)}
                  disabled={loadingAllJobs}
                  className="px-3 py-2 bg-secondary hover:bg-secondary/80 text-foreground border border-border rounded-xl text-xs font-semibold transition-colors flex items-center gap-1.5 cursor-pointer"
                  title="Làm mới danh sách dự án"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingAllJobs ? "animate-spin" : ""}`} />
                  <span className="hidden sm:inline">Làm mới</span>
                </button>

                <button
                  type="button"
                  onClick={() => setViewMode("create")}
                  className="px-3.5 py-2 bg-primary hover:bg-primary/90 text-primary-foreground rounded-xl text-xs font-bold shadow-sm transition-all flex items-center gap-1.5 cursor-pointer"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>Dự Án Mới</span>
                </button>
              </div>
            </div>

            {/* 3. Data Table Body */}
            {loadingAllJobs && allJobs.length === 0 ? (
              <div className="p-14 flex flex-col items-center justify-center text-center gap-3">
                <Loader2 className="w-7 h-7 text-primary animate-spin" />
                <span className="text-xs text-muted-foreground font-medium">Đang tải bảng quản lý dự án...</span>
              </div>
            ) : filteredJobs.length === 0 ? (
              <div className="p-14 flex flex-col items-center justify-center text-center gap-3">
                <FolderOpen className="w-10 h-10 text-muted-foreground/40" />
                <div>
                  <h4 className="text-sm font-bold text-foreground">Không có dự án nào phù hợp bộ lọc</h4>
                  <p className="text-xs text-muted-foreground mt-1">
                    {jobsSearch || jobsFilter !== "all"
                      ? "Thử xóa từ khóa tìm kiếm hoặc chọn lại tab Tất cả dự án."
                      : "Bắt đầu bằng cách tải lên tệp video hoặc dán link YouTube."}
                  </p>
                </div>
                <div className="flex items-center gap-2 mt-1">
                  {(jobsSearch || jobsFilter !== "all") && (
                    <button
                      type="button"
                      onClick={() => {
                        setJobsFilter("all");
                        setJobsSearch("");
                        setJobsPage(1);
                      }}
                      className="px-3.5 py-2 bg-secondary hover:bg-secondary/80 text-foreground text-xs font-semibold rounded-xl border border-border cursor-pointer"
                    >
                      Xem tất cả ({allJobs.length})
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setViewMode("create")}
                    className="px-4 py-2 bg-primary hover:bg-primary/90 text-primary-foreground font-bold text-xs rounded-xl shadow flex items-center gap-1.5 cursor-pointer"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Tạo Dự Án Mới</span>
                  </button>
                </div>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-secondary/35 border-b border-border text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                      <th className="py-3 pl-4 pr-2 w-10">
                        <input
                          type="checkbox"
                          checked={allVisibleSelected}
                          onChange={toggleSelectAllVisible}
                          className="w-3.5 h-3.5 accent-primary rounded cursor-pointer"
                          title="Chọn tất cả dự án trên trang này"
                        />
                      </th>
                      <th className="py-3 px-3 min-w-[280px]">Dự Án &amp; Video</th>
                      <th className="py-3 px-3 min-w-[155px]">Ngôn Ngữ &amp; Giọng</th>
                      <th className="py-3 px-3 min-w-[185px]">Trạng Thái &amp; Tiến Độ</th>
                      <th className="py-3 px-3 min-w-[120px] whitespace-nowrap">Thời Gian</th>
                      <th className="py-3 pl-3 pr-4 text-right min-w-[195px]">Thao Tác</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/70 text-xs">
                    {paginatedJobs.map((j) => {
                      const isReview = j.status === "awaiting_review";
                      const isCompleted = j.status === "completed";
                      const isFailed = j.status === "failed";
                      const isUploaded = j.status === "uploaded";
                      const isRunning = !isCompleted && !isFailed && !isReview && !isUploaded;
                      const isYt = j.source_type === "youtube";
                      const ytId = getYouTubeId(j.source_url);
                      const durLabel = formatDuration(j.video_duration);
                      const statusMeta = getStatusMeta(j.status, j.progress || 0);
                      const isChecked = selectedJobIds.includes(j.id);
                      const isCurrentActive = jobId === j.id;
                      const voiceObj = j.voice_sample_id ? voiceSamples.find(v => v.id === j.voice_sample_id) : null;
                      const voiceLabel =
                        j.voice_mode === "clone_original"
                          ? "Clone gốc"
                          : voiceObj?.name || j.voice_sample_id || "Bản ngữ tự động";

                      return (
                        <tr
                          key={j.id}
                          onClick={() => openProjectInWorkspace(j.id, j)}
                          className={`group transition-colors cursor-pointer ${
                            isChecked
                              ? "bg-primary/5"
                              : isCurrentActive
                                ? "bg-primary/[0.03] hover:bg-secondary/40"
                                : "hover:bg-secondary/35"
                          }`}
                        >
                          {/* Checkbox */}
                          <td className="py-3 pl-4 pr-2 align-middle" onClick={(e) => e.stopPropagation()}>
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={() => toggleSelectJob(j.id)}
                              className="w-3.5 h-3.5 accent-primary rounded cursor-pointer"
                            />
                          </td>

                          {/* Project Thumbnail + Title + Metadata */}
                          <td className="py-3 px-3 align-middle">
                            <div className="flex items-center gap-3 min-w-0">
                              <div className="relative w-20 h-11 rounded-lg overflow-hidden bg-secondary/60 border border-border/70 shrink-0 flex items-center justify-center">
                                {isYt && j.video_thumbnail ? (
                                  <img
                                    src={j.video_thumbnail}
                                    alt={j.video_title || "Thumbnail"}
                                    className="w-full h-full object-cover"
                                    loading="lazy"
                                  />
                                ) : (
                                  <Film className={`w-4 h-4 ${isYt ? "text-red-500" : "text-primary/70"}`} />
                                )}
                                {durLabel && (
                                  <span className="absolute bottom-0.5 right-0.5 px-1 py-0.2 rounded bg-black/80 text-white text-[9px] font-mono font-bold leading-tight">
                                    {durLabel}
                                  </span>
                                )}
                              </div>

                              <div className="flex flex-col min-w-0 flex-1 gap-0.5">
                                <div className="flex items-center gap-1.5 min-w-0">
                                  <span className="font-bold text-foreground group-hover:text-primary transition-colors truncate max-w-[300px] sm:max-w-[360px]">
                                    {j.video_title || (j.source_url ? j.source_url : `Dự án #${j.id.slice(0, 8)}`)}
                                  </span>
                                </div>
                                <div className="flex items-center gap-1.5 flex-wrap text-[10px] text-muted-foreground">
                                  <span className="font-mono">#{j.id.slice(0, 8)}</span>
                                  <span>•</span>
                                  <span className={`font-semibold ${isYt ? "text-red-500" : "text-primary"}`}>
                                    {isYt ? "YouTube" : "Tải lên"}
                                  </span>
                                  {isYt && j.preferred_quality && (
                                    <span className="px-1.5 py-0.2 rounded bg-secondary border border-border font-mono text-[9px] font-bold text-foreground">
                                      {j.preferred_quality === "2160p" ? "4K" : j.preferred_quality === "1440p" ? "2K" : j.preferred_quality}
                                    </span>
                                  )}
                                  {isYt && ytId && (
                                    <a
                                      href={j.source_url || ""}
                                      target="_blank"
                                      rel="noreferrer"
                                      onClick={(e) => e.stopPropagation()}
                                      className="inline-flex items-center gap-0.5 hover:text-red-500 transition-colors"
                                      title="Mở video gốc trên YouTube"
                                    >
                                      <ExternalLink className="w-2.5 h-2.5" />
                                    </a>
                                  )}
                                </div>
                              </div>
                            </div>
                          </td>

                          {/* Target Language & Voice */}
                          <td className="py-3 px-3 align-middle">
                            <div className="flex flex-col gap-1">
                              <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-foreground">
                                <Languages className="w-3 h-3 text-primary shrink-0" />
                                <span>{j.target_language}</span>
                              </span>
                              <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground truncate max-w-[155px]" title={voiceLabel}>
                                <Mic className="w-2.5 h-2.5 shrink-0" />
                                <span className="truncate">{voiceLabel}</span>
                              </span>
                            </div>
                          </td>

                          {/* Status & Inline Progress */}
                          <td className="py-3 px-3 align-middle">
                            <div className="flex flex-col gap-1.5 max-w-[210px]">
                              <span className={`inline-flex items-center gap-1.5 w-fit px-2 py-0.5 rounded-full text-[10px] font-bold border ${statusMeta.badge}`}>
                                {isRunning ? (
                                  <Loader2 className="w-2.5 h-2.5 animate-spin shrink-0" />
                                ) : (
                                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusMeta.dot}`} />
                                )}
                                <span className="whitespace-nowrap">{statusMeta.label}</span>
                              </span>

                              {isRunning && (
                                <div className="flex flex-col gap-0.5">
                                  <div className="w-full bg-secondary h-1.5 rounded-full overflow-hidden">
                                    <div
                                      className="bg-primary h-full transition-all duration-300"
                                      style={{ width: `${Math.min(100, Math.max(4, j.progress || 0))}%` }}
                                    />
                                  </div>
                                  <span className="text-[10px] text-muted-foreground truncate" title={j.message || ""}>
                                    {j.message || "Đang xử lý..."}
                                  </span>
                                </div>
                              )}

                              {isFailed && (j.error_message || j.message) && (
                                <span className="text-[10px] text-destructive/85 truncate" title={j.error_message || j.message || ""}>
                                  {j.error_message || j.message}
                                </span>
                              )}
                            </div>
                          </td>

                          {/* Created Time */}
                          <td className="py-3 px-3 align-middle whitespace-nowrap text-[11px] text-muted-foreground tabular-nums">
                            {j.created_at
                              ? new Date(j.created_at).toLocaleString("vi-VN", {
                                  day: "2-digit",
                                  month: "2-digit",
                                  year: "2-digit",
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })
                              : "—"}
                          </td>

                          {/* Row Actions */}
                          <td className="py-3 pl-3 pr-4 align-middle" onClick={(e) => e.stopPropagation()}>
                            <div className="flex items-center justify-end gap-1.5">
                              <button
                                type="button"
                                onClick={() => openProjectInWorkspace(j.id, j)}
                                className={`px-2.5 py-1.5 rounded-lg text-[11px] font-bold transition-all flex items-center gap-1 cursor-pointer whitespace-nowrap border ${
                                  isReview
                                    ? "bg-amber-500 hover:bg-amber-600 text-white border-amber-500 shadow-sm"
                                    : isCompleted
                                      ? "bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 border-emerald-500/30"
                                      : isFailed
                                        ? "bg-destructive/10 hover:bg-destructive/20 text-destructive border-destructive/30"
                                        : "bg-secondary hover:bg-secondary/80 text-foreground border-border"
                                }`}
                              >
                                <Eye className="w-3 h-3 shrink-0" />
                                <span>{statusMeta.actionLabel}</span>
                              </button>

                              {isCompleted ? (
                                <a
                                  href={api.getDubbingFileUrl(j.id, "output", true)}
                                  download={`dubbed_${j.id}.mp4`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="p-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 rounded-lg border border-emerald-500/30 transition-colors"
                                  title="Tải video MP4 đã lồng tiếng"
                                >
                                  <Download className="w-3.5 h-3.5" />
                                </a>
                              ) : j.has_source_video ? (
                                <a
                                  href={api.getDubbingFileUrl(j.id, "video", true)}
                                  download={`youtube_${j.id}.mp4`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="p-1.5 bg-sky-500/10 hover:bg-sky-500/20 text-sky-600 dark:text-sky-400 rounded-lg border border-sky-500/30 transition-colors"
                                  title="Tải video gốc MP4"
                                >
                                  <Download className="w-3.5 h-3.5" />
                                </a>
                              ) : null}

                              <button
                                type="button"
                                onClick={() => void handleDeleteJob(j.id)}
                                className="p-1.5 hover:bg-destructive/10 text-muted-foreground hover:text-destructive rounded-lg transition-colors cursor-pointer"
                                title="Xóa dự án này"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* 4. Pagination Footer */}
            {filteredJobs.length > 0 && (
              <div className="px-4 py-3 border-t border-border bg-secondary/20 flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-muted-foreground">
                <div className="flex items-center gap-2">
                  <span>
                    Hiển thị{" "}
                    <strong className="text-foreground">
                      {jobsPageSize >= 999 ? 1 : (safeJobsPage - 1) * jobsPageSize + 1}–
                      {jobsPageSize >= 999 ? filteredJobs.length : Math.min(safeJobsPage * jobsPageSize, filteredJobs.length)}
                    </strong>{" "}
                    trong <strong className="text-foreground">{filteredJobs.length}</strong> dự án
                  </span>
                  <select
                    value={jobsPageSize}
                    onChange={(e) => {
                      setJobsPageSize(Number(e.target.value));
                      setJobsPage(1);
                    }}
                    className="bg-background border border-border rounded-lg px-2 py-1 text-[11px] text-foreground focus:outline-none cursor-pointer"
                  >
                    <option value={15}>15 / trang</option>
                    <option value={30}>30 / trang</option>
                    <option value={50}>50 / trang</option>
                    <option value={999}>Tất cả</option>
                  </select>
                </div>

                {totalJobPages > 1 && (
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setJobsPage(p => Math.max(1, p - 1))}
                      disabled={safeJobsPage <= 1}
                      className="p-1.5 rounded-lg border border-border bg-background hover:bg-secondary text-foreground disabled:opacity-40 cursor-pointer"
                      title="Trang trước"
                    >
                      <ChevronLeft className="w-3.5 h-3.5" />
                    </button>
                    <span className="px-2 text-[11px] font-semibold text-foreground tabular-nums">
                      Trang {safeJobsPage} / {totalJobPages}
                    </span>
                    <button
                      type="button"
                      onClick={() => setJobsPage(p => Math.min(totalJobPages, p + 1))}
                      disabled={safeJobsPage >= totalJobPages}
                      className="p-1.5 rounded-lg border border-border bg-background hover:bg-secondary text-foreground disabled:opacity-40 cursor-pointer"
                      title="Trang sau"
                    >
                      <ChevronRight className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* VIEW MODE 1: FORM SETUP / CREATE NEW PROJECT (viewMode === "create")      */}
      {/* ========================================================================= */}
      {viewMode === "create" && (
        <div className="flex flex-col gap-6 animate-fadeIn">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <SectionCard title="Tải Lên Video Đầu Vào">
              <form onSubmit={handleStartDubbing} className="flex flex-col gap-5">
                
                {/* Drag and Drop Card with Progress & Preview */}
                {!uploadedJobId && !uploading ? (
                  <div className="border-2 border-dashed border-border hover:border-primary/50 transition-colors rounded-2xl p-8 flex flex-col items-center justify-center cursor-pointer bg-secondary/20 relative group">
                    <input
                      type="file"
                      accept="video/*"
                      onChange={handleFileChange}
                      className="absolute inset-0 opacity-0 cursor-pointer"
                    />
                    <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center text-primary mb-3 group-hover:scale-110 transition-transform">
                      <Upload className="w-6 h-6" />
                    </div>
                    <span className="text-xs font-bold text-foreground">
                      Tải lên tệp video từ máy tính
                    </span>
                    <span className="text-[10px] text-muted-foreground mt-1">Hỗ trợ các định dạng MP4, MKV, MOV</span>
                  </div>
                ) : uploading ? (
                  <div className="border-2 border-dashed border-primary/45 rounded-2xl p-8 flex flex-col items-center justify-center bg-primary/5">
                    <Loader2 className="w-8 h-8 text-primary animate-spin mb-3" />
                    <span className="text-xs font-bold text-foreground mb-2">Đang tải video lên máy chủ... {uploadProgress}%</span>
                    <div className="w-full max-w-xs bg-secondary h-2 rounded-full overflow-hidden border border-border">
                      <div className="bg-primary h-full transition-all duration-300" style={{ width: `${uploadProgress}%` }}></div>
                    </div>
                  </div>
                ) : (
                  <div className="border border-border rounded-2xl p-5 flex flex-col gap-3 bg-secondary/15">
                    <div className="flex justify-between items-center gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                        <span className="text-xs font-bold text-foreground truncate max-w-xs sm:max-w-md">
                          {selectedFile ? selectedFile.name : (uploadedVideoTitle || "Video đã được tải lên")}
                        </span>
                      </div>
                      <div className="flex items-center gap-2.5 shrink-0">
                        {uploadedJobId && (
                          <a
                            href={api.getDubbingFileUrl(uploadedJobId, "video", true)}
                            download={`youtube_${uploadedJobId}.mp4`}
                            className="text-[11px] text-emerald-600 dark:text-emerald-400 hover:underline font-bold flex items-center gap-1"
                          >
                            <Download className="w-3 h-3" />
                            <span>Tải MP4 Về Máy</span>
                          </a>
                        )}
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedFile(null);
                            setUploadedJobId(null);
                            setUploadedVideoUrl(null);
                            setUploadedVideoTitle(null);
                            setYtDownloadJobId(null);
                          }}
                          className="text-[10px] text-destructive hover:underline font-semibold cursor-pointer"
                        >
                          Chọn video khác
                        </button>
                      </div>
                    </div>
                    {uploadedVideoUrl && (
                      <div className="aspect-video w-full rounded-xl overflow-hidden bg-black border border-border relative">
                        <video
                          src={uploadedVideoUrl}
                          controls
                          className="w-full h-full object-contain"
                        />
                      </div>
                    )}
                  </div>
                )}

                <div className="flex items-center my-1">
                  <div className="flex-grow border-t border-border"></div>
                  <span className="mx-3 text-[10px] text-muted-foreground font-bold tracking-wider uppercase">HOẶC DÁN LINK YOUTUBE</span>
                  <div className="flex-grow border-t border-border"></div>
                </div>

                {/* YouTube Link */}
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <label className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5">
                      <PlaySquare className="w-3.5 h-3.5 text-red-500" />
                      <span>Đường dẫn YouTube (Watch, Shorts, Live, youtu.be)</span>
                    </label>
                    {isAdmin && (
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => {
                            setActiveModalTab("proxy");
                            setShowCookieModal(true);
                          }}
                          className={`text-[11px] px-2.5 py-1 rounded-lg border flex items-center gap-1.5 font-medium transition-all cursor-pointer ${
                            proxyPool.length > 0
                              ? "bg-purple-500/15 text-purple-600 dark:text-purple-400 border-purple-500/30 hover:bg-purple-500/25"
                              : "bg-secondary/60 text-muted-foreground border-border hover:bg-secondary hover:text-foreground"
                          }`}
                        >
                          <Globe className={`w-3.5 h-3.5 ${proxyPool.length > 0 ? "text-purple-500" : "text-muted-foreground"}`} />
                          <span>{proxyPool.length > 0 ? `Proxy Pool (${proxyPool.length})` : "Proxy Dân Cư"}</span>
                        </button>

                        <button
                          type="button"
                          onClick={() => {
                            setActiveModalTab(oauthStatus?.connected ? "oauth" : cookieStatus?.has_cookies ? "cookies" : "oauth");
                            setShowCookieModal(true);
                          }}
                          className={`text-[11px] px-2.5 py-1 rounded-lg border flex items-center gap-1.5 font-medium transition-all cursor-pointer ${
                            oauthStatus?.connected
                              ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/25"
                              : cookieStatus?.has_cookies
                              ? "bg-blue-500/15 text-blue-600 dark:text-blue-400 border-blue-500/30 hover:bg-blue-500/25"
                              : "bg-primary/15 text-primary border-primary/30 hover:bg-primary/25"
                          }`}
                        >
                          {oauthStatus?.connected ? (
                            <>
                              <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />
                              <span>YouTube: Đã kết nối</span>
                            </>
                          ) : cookieStatus?.has_cookies ? (
                            <>
                              <Cookie className="w-3.5 h-3.5 text-blue-500" />
                              <span>Cookie: Đã cài đặt</span>
                            </>
                          ) : (
                            <>
                              <Key className="w-3.5 h-3.5 text-primary" />
                              <span>Kết nối YouTube</span>
                            </>
                          )}
                        </button>
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      type="url"
                      placeholder="https://www.youtube.com/watch?v=... hoặc youtube.com/shorts/..."
                      value={youtubeUrl}
                      onChange={(e) => {
                        setYoutubeUrl(e.target.value);
                        if (uploadedJobId && ytDownloadJobId === uploadedJobId) {
                          setUploadedJobId(null);
                          setUploadedVideoUrl(null);
                          setUploadedVideoTitle(null);
                          setYtDownloadJobId(null);
                        }
                      }}
                      className="flex-1 bg-background border border-border rounded-xl px-4 py-2.5 text-xs text-foreground focus:outline-none focus:border-primary"
                    />
                    <button
                      type="button"
                      onClick={() => void handleCheckYouTubeInfo()}
                      disabled={!youtubeUrl.trim() || ytInfoLoading}
                      className="px-3.5 py-2.5 bg-secondary hover:bg-secondary/80 text-foreground text-xs font-bold rounded-xl border border-border transition-colors flex items-center gap-1.5 shrink-0 disabled:opacity-50 cursor-pointer"
                    >
                      {ytInfoLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" /> : <Search className="w-3.5 h-3.5 text-primary" />}
                      <span>Kiểm tra</span>
                    </button>
                  </div>

                  {ytInfoLoading && !ytInfo && (
                    <div className="p-3 bg-secondary/30 border border-border rounded-xl text-xs text-muted-foreground flex items-center gap-2">
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
                      <span>Đang lấy thông tin video từ YouTube...</span>
                    </div>
                  )}

                  {ytInfoError && (
                    <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-xl text-xs text-amber-600 dark:text-amber-400 flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4 shrink-0" />
                      <span>{ytInfoError}</span>
                    </div>
                  )}

                  {ytInfo && (
                    <div className="p-4 bg-secondary/20 border border-border rounded-2xl flex flex-col gap-3">
                      <div className="flex flex-col sm:flex-row gap-3.5">
                        {ytInfo.thumbnail_url && (
                          <div className="relative w-full sm:w-44 aspect-video rounded-xl overflow-hidden bg-black shrink-0 border border-border">
                            <img
                              src={ytInfo.thumbnail_url}
                              alt={ytInfo.title}
                              className="w-full h-full object-cover"
                            />
                            {formatDuration(ytInfo.duration) && (
                              <span className="absolute bottom-1.5 right-1.5 px-1.5 py-0.5 rounded bg-black/80 text-white text-[10px] font-mono font-bold">
                                {formatDuration(ytInfo.duration)}
                              </span>
                            )}
                          </div>
                        )}
                        <div className="flex flex-col justify-between gap-2 min-w-0 flex-1">
                          <div>
                            <h4 className="text-xs font-bold text-foreground line-clamp-2 leading-snug">
                              {ytInfo.title}
                            </h4>
                            <div className="flex items-center gap-2 text-[11px] text-muted-foreground mt-1 flex-wrap">
                              {ytInfo.channel && <span className="font-medium text-foreground/80">{ytInfo.channel}</span>}
                              {formatDuration(ytInfo.duration) && <span>• {formatDuration(ytInfo.duration)}</span>}
                              <a
                                href={ytInfo.canonical_url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1 text-primary hover:underline"
                              >
                                <span>Mở YouTube</span>
                                <ExternalLink className="w-2.5 h-2.5" />
                              </a>
                            </div>
                          </div>

                          {ytInfo.is_live && (
                            <div className="p-2 bg-amber-500/10 border border-amber-500/30 rounded-lg text-[11px] text-amber-600 dark:text-amber-400 flex items-center gap-1.5">
                              <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                              <span>Video đang phát trực tiếp (Live) có thể không tải được toàn bộ.</span>
                            </div>
                          )}

                          <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                            <div className="flex items-center gap-2">
                              <label className="text-[11px] font-semibold text-muted-foreground">Chất lượng:</label>
                              <select
                                value={preferredQuality}
                                onChange={(e) => setPreferredQuality(e.target.value)}
                                disabled={ytDownloading}
                                className="bg-background border border-border rounded-lg px-2.5 py-1 text-xs font-medium text-foreground focus:outline-none focus:border-primary"
                              >
                                {(ytInfo.available_qualities && ytInfo.available_qualities.length > 0
                                  ? ytInfo.available_qualities
                                  : ["2160p", "1440p", "1080p", "720p", "480p", "360p"]
                                ).map((q) => (
                                  <option key={q} value={q}>
                                    {q === "2160p"
                                      ? "4K Ultra HD (2160p)"
                                      : q === "1440p"
                                        ? "2K QHD (1440p)"
                                        : q === "1080p"
                                          ? "1080p Full HD"
                                          : q === "720p"
                                            ? "720p HD"
                                            : q === "480p"
                                              ? "480p SD"
                                              : "360p Nhanh"}
                                  </option>
                                ))}
                              </select>
                            </div>

                            <button
                              type="button"
                              onClick={() => void handlePreDownloadYouTube()}
                              disabled={ytDownloading || Boolean(uploadedJobId && ytDownloadJobId === uploadedJobId)}
                              className="px-3 py-1.5 bg-primary/10 hover:bg-primary/20 text-primary text-xs font-bold rounded-xl border border-primary/25 transition-all flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
                            >
                              {ytDownloading ? (
                                <>
                                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                  <span>Đang tải ({ytDownloadProgress}%)</span>
                                </>
                              ) : uploadedJobId && ytDownloadJobId === uploadedJobId ? (
                                <>
                                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                                  <span>Đã tải lên máy chủ</span>
                                </>
                              ) : (
                                <>
                                  <Download className="w-3.5 h-3.5" />
                                  <span>Tải Video Về Máy Chủ Trước</span>
                                </>
                              )}
                            </button>
                          </div>
                        </div>
                      </div>

                      {ytDownloading && (
                        <div className="flex flex-col gap-1.5 pt-1 border-t border-border/50">
                          <div className="flex items-center justify-between text-[11px]">
                            <span className="text-foreground font-medium truncate">{ytDownloadMessage || "Đang tải video từ YouTube..."}</span>
                            <span className="font-mono font-bold text-primary shrink-0">{ytDownloadProgress}%</span>
                          </div>
                          <div className="w-full bg-secondary h-2 rounded-full overflow-hidden border border-border">
                            <div
                              className="bg-primary h-full transition-all duration-300"
                              style={{ width: `${ytDownloadProgress}%` }}
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Native Vietnamese Voice Selector */}
                {renderVoiceSelector(false)}

                {/* Smart separation toggle */}
                <div className="flex items-center gap-3 p-3.5 bg-secondary/30 rounded-xl border border-border">
                  <input
                    type="checkbox"
                    id="smart-sep"
                    checked={smartSeparation}
                    onChange={(e) => setSmartSeparation(e.target.checked)}
                    className="w-4 h-4 accent-primary rounded cursor-pointer"
                  />
                  <label htmlFor="smart-sep" className="text-xs text-foreground cursor-pointer flex-grow">
                    <span className="font-bold block">Tách giọng nói & nhạc nền (Demucs GPU)</span>
                    <span className="text-[10px] text-muted-foreground">Cô lập thoại để clone giọng chuẩn, giữ nguyên nhạc hiệu ứng.</span>
                  </label>
                </div>

                <button
                  type="submit"
                  disabled={loading}
                  className="w-full py-3 bg-gradient-to-r from-primary to-accent hover:brightness-105 text-white font-bold text-xs rounded-xl transition-all shadow-md shadow-primary/10 flex items-center justify-center gap-2 disabled:opacity-50 cursor-pointer"
                >
                  {loading ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Đang khởi tạo tác vụ...</span>
                    </>
                  ) : (
                    <>
                      <Sparkles className="w-4 h-4" />
                      <span>Bắt Đầu Nhận Dạng & Dịch Thuật</span>
                    </>
                  )}
                </button>

              </form>
            </SectionCard>
          </div>

          {/* Right Configuration Sidecard */}
          <div className="flex flex-col gap-6">
            <SectionCard title="Cấu Hình Dịch Thuật">
              <div className="flex flex-col gap-4">
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-semibold text-muted-foreground">Ngôn ngữ đích</label>
                  <select
                    value={targetLanguage}
                    onChange={(e) => setTargetLanguage(e.target.value)}
                    className="bg-background border border-border rounded-xl px-3.5 py-2.5 text-xs text-foreground focus:outline-none focus:border-primary font-medium"
                  >
                    <option value="English">English (Tiếng Anh)</option>
                    <option value="Vietnamese">Tiếng Việt (Vietnamese)</option>
                    <option value="Japanese">日本語 (Japanese)</option>
                    <option value="Korean">한국어 (Korean)</option>
                    <option value="Chinese">中文 (Chinese)</option>
                    <option value="French">Français (French)</option>
                    <option value="Spanish">Español (Spanish)</option>
                  </select>
                </div>

                {/* LLM Admin Info Badge / Selector */}
                <div className="p-3.5 bg-secondary/30 rounded-xl border border-border flex flex-col gap-2.5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <Key className="w-3.5 h-3.5 text-primary" />
                      <span className="text-xs font-bold text-foreground">Mô hình Dịch AI (LLM)</span>
                    </div>
                    {llmProfiles.length > 0 && (
                      <span className="text-[10px] text-muted-foreground bg-secondary px-2 py-0.5 rounded-full border border-border/60 font-semibold">
                        {llmProfiles.length} Profile LLM
                      </span>
                    )}
                  </div>

                  {llmProfiles.length > 0 ? (
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-semibold text-muted-foreground">Chọn Profile LLM muốn sử dụng:</label>
                      <select
                        value={selectedLlmProfileId}
                        onChange={(e) => setSelectedLlmProfileId(e.target.value)}
                        className="w-full bg-background border border-border rounded-lg px-2.5 py-1.5 text-xs text-foreground font-medium focus:ring-1 focus:ring-primary outline-hidden cursor-pointer"
                      >
                        {llmProfiles.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name} ({p.provider.toUpperCase()} - {p.model}) {p.is_active ? " [Mặc định]" : ""}
                          </option>
                        ))}
                      </select>
                    </div>
                  ) : (
                    <div className="text-[10px] text-muted-foreground flex flex-col gap-1">
                      <span>Provider: <strong className="text-foreground">{llmProvider.toUpperCase()}</strong></span>
                      <span>Model: <strong className="text-foreground">{llmModel}</strong></span>
                      {llmCustomEndpoint && (
                        <span className="truncate">Endpoint: <strong className="text-foreground font-mono">{llmCustomEndpoint}</strong></span>
                      )}
                    </div>
                  )}

                  <span className="text-[9px] text-primary/80 italic mt-0.5">
                    Quản lý, Thêm/Sửa API Key & Profile LLM tại Admin Portal -&gt; Tab Cấu hình LLM.
                  </span>
                </div>

                <div className="p-3.5 bg-secondary/20 rounded-xl border border-border/50 text-[11px] text-muted-foreground flex flex-col gap-1.5">
                  <span className="font-bold text-foreground">Quy trình tự động gồm:</span>
                  <p>1. Tách nhạc nền & giọng thoại (Demucs)</p>
                  <p>2. Chuyển đổi thoại thành văn bản (Whisper)</p>
                  <p>3. Dịch văn bản qua LLM</p>
                  <p>4. Chờ xem trước & duyệt phụ đề</p>
                  <p>5. Sinh giọng đọc clone đè đồng bộ (OmniVoice)</p>
                </div>
              </div>
            </SectionCard>
          </div>
        </div>
        </div>
      )}

      {/* --- STEP 3: SUBTITLE REVIEW & EDIT STUDIO --- */}
      {viewMode === "workspace" && jobId && job && job.id === jobId && job.status === "awaiting_review" && (() => {
        const activeSegIdx = originalSubs.findIndex((s, i) => {
          const st = typeof s?.start === "number" ? s.start : (translatedSubs[i]?.start ?? -1);
          const et = typeof s?.end === "number" ? s.end : (translatedSubs[i]?.end ?? -1);
          return currentVideoTime >= st && currentVideoTime <= et;
        });
        const fallbackIdx = activeSegIdx === -1
          ? translatedSubs.findIndex(s => typeof s.start === "number" && typeof s.end === "number" && currentVideoTime >= s.start && currentVideoTime <= s.end)
          : activeSegIdx;
        const activeOrigSeg = fallbackIdx !== -1 ? (originalSubs[fallbackIdx] || translatedSubs[fallbackIdx]) : null;
        const activeTransSeg = fallbackIdx !== -1 ? (translatedSubs[fallbackIdx] || originalSubs[fallbackIdx]) : null;

        return (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          
          {/* Left: Video Player & Audio Controls */}
          <SectionCard title="Video Xem Trước & Bộ Trộn Âm Thanh">
            <div className="flex flex-col gap-4">
              <div className="relative aspect-video rounded-xl bg-black overflow-hidden border border-border shadow-inner">
                <video
                  ref={videoPlayerRef}
                  src={api.getDubbingFileUrl(jobId, "video")}
                  controls
                  playsInline
                  preload="metadata"
                  muted={Boolean(job.vocals_audio_path || job.bgm_audio_path)}
                  onPlay={handleVideoPlay}
                  onPause={handleVideoPause}
                  onSeeked={handleVideoSeek}
                  onRateChange={handleVideoRateChange}
                  onTimeUpdate={handleVideoTimeUpdate}
                  onWaiting={handleVideoWaiting}
                  onPlaying={handleVideoPlaying}
                  onVolumeChange={handleVideoVolumeChange}
                  className="w-full h-full object-contain"
                />

                {/* Live Subtitle Overlay on Video Player (English ASR + Translation) */}
                {subOverlayMode !== "off" && activeOrigSeg && (
                  <div className="absolute bottom-11 left-3 right-3 pointer-events-none z-10 flex flex-col items-center gap-1 transition-all">
                    <span className="px-2 py-0.5 rounded-full bg-black/80 text-[9px] font-mono font-bold text-amber-300 border border-amber-400/30 shadow">
                      Phân đoạn #{activeOrigSeg.id} • {activeOrigSeg.start.toFixed(2)}s → {activeOrigSeg.end.toFixed(2)}s
                    </span>
                    {(subOverlayMode === "bilingual" || subOverlayMode === "en") && (
                      <div className="max-w-[95%] bg-black/85 backdrop-blur-md text-white px-3.5 py-1.5 rounded-lg border border-amber-400/50 shadow-lg text-center">
                        <span className="text-[9px] font-extrabold uppercase tracking-wider text-amber-400 mr-1.5">[SUB GỐC ASR]</span>
                        <span className="text-xs sm:text-sm font-semibold leading-snug">{activeOrigSeg.text}</span>
                      </div>
                    )}
                    {(subOverlayMode === "bilingual" || subOverlayMode === "vi") && activeTransSeg && (
                      <div className="max-w-[92%] bg-black/80 backdrop-blur-md text-emerald-300 px-3 py-1 rounded-lg border border-emerald-500/40 shadow-md text-center">
                        <span className="text-[9px] font-bold uppercase tracking-wider text-emerald-400 mr-1.5">[DỊCH]</span>
                        <span className="text-[11px] sm:text-xs font-medium leading-snug">{activeTransSeg.text}</span>
                      </div>
                    )}
                  </div>
                )}
                
                {job.vocals_audio_path && (
                  <audio ref={vocalsPlayerRef} src={api.getDubbingFileUrl(jobId, "vocals", false, true)} preload="metadata" />
                )}
                {job.bgm_audio_path && (
                  <audio ref={bgmPlayerRef} src={api.getDubbingFileUrl(jobId, "bgm", false, true)} preload="metadata" />
                )}
              </div>

              {/* Dedicated Live English ASR Monitor Bar & Mode Switcher */}
              <div className="p-3 bg-secondary/40 rounded-xl border border-border flex flex-col gap-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded bg-background border border-border text-[10px] font-mono font-bold text-primary">
                      ⏱ {currentVideoTime.toFixed(2)}s
                    </span>
                    {activeOrigSeg ? (
                      <span className="text-[11px] font-bold text-amber-600 dark:text-amber-400 flex items-center gap-1">
                        <span className="w-2 h-2 rounded-full bg-amber-500 animate-ping" />
                        Đang phát #{activeOrigSeg.id} ({activeOrigSeg.start.toFixed(1)}s - {activeOrigSeg.end.toFixed(1)}s)
                      </span>
                    ) : (
                      <span className="text-[11px] text-muted-foreground italic">
                        Khoảng lặng / Nhạc nền (Không có sub ASR tại mốc này)
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-1">
                    {(
                      [
                        { id: "bilingual", label: "EN (ASR) + VI" },
                        { id: "en", label: "Chỉ Sub EN (ASR)" },
                        { id: "vi", label: "Chỉ Sub VI" },
                        { id: "off", label: "Tắt Sub" },
                      ] as const
                    ).map((m) => (
                      <button
                        key={m.id}
                        type="button"
                        onClick={() => setSubOverlayMode(m.id)}
                        className={`px-2 py-0.5 rounded text-[10px] font-bold transition-colors cursor-pointer border ${
                          subOverlayMode === m.id
                            ? "bg-primary text-primary-foreground border-primary"
                            : "bg-background hover:bg-secondary text-muted-foreground border-border"
                        }`}
                      >
                        {m.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Persistent English ASR Sub Box below video so native controls never obscure it */}
                <div className="p-2.5 rounded-lg bg-background/90 border border-amber-500/30 flex flex-col gap-1 min-h-[52px] justify-center">
                  {activeOrigSeg ? (
                    <>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[9px] font-extrabold uppercase tracking-wider text-amber-600 dark:text-amber-400">
                          Sub Gốc Tiếng Anh (Whisper ASR • Phân đoạn #{activeOrigSeg.id}):
                        </span>
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedSegId(activeOrigSeg.id);
                            setEditingOrigSegId(activeOrigSeg.id);
                          }}
                          className="text-[10px] text-primary hover:underline font-bold cursor-pointer"
                        >
                          Sửa sub gốc này
                        </button>
                      </div>
                      <p className="text-xs font-semibold text-foreground leading-snug">
                        {activeOrigSeg.text}
                      </p>
                    </>
                  ) : (
                    <p className="text-[11px] text-muted-foreground italic text-center">
                      Bấm Play video hoặc chọn một phân đoạn bên phải để đối chiếu trực tiếp phụ đề Tiếng Anh (ASR) với giọng nói gốc.
                    </p>
                  )}
                </div>
              </div>

              {/* Audio Track Mix Panel */}
              <div className="p-4 bg-secondary/30 rounded-xl border border-border flex flex-col gap-4">
                <div className="flex items-center justify-between border-b border-border/60 pb-2.5">
                  <div className="flex items-center gap-2">
                    <Sliders className="w-4 h-4 text-primary" />
                    <h4 className="text-xs font-bold uppercase tracking-wider text-foreground">Bộ trộn tách kênh âm thanh</h4>
                    <span className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
                      Live Sync
                    </span>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={handleReseparateAudio}
                      disabled={reseparating}
                      title="Chạy lại thuật toán tách giọng và nhạc nền"
                      className="px-2 py-1 bg-secondary hover:bg-secondary/80 text-foreground text-[10px] font-medium rounded-lg border border-border flex items-center gap-1 transition-colors disabled:opacity-50 cursor-pointer"
                    >
                      {reseparating ? (
                        <>
                          <Loader2 className="w-3 h-3 animate-spin text-primary" />
                          <span>Đang tách...</span>
                        </>
                      ) : (
                        <>
                          <RefreshCw className="w-3 h-3 text-primary" />
                          <span>Tách Lại Stems</span>
                        </>
                      )}
                    </button>

                    <button
                      onClick={handleResetMix}
                      title="Khôi phục âm lượng mặc định"
                      className="p-1 hover:bg-secondary rounded-lg text-muted-foreground hover:text-foreground transition-colors"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                <div className="flex flex-col gap-3.5">
                  {/* Track 1: Vocals */}
                  <div className="p-2.5 rounded-lg bg-background/60 border border-border/50 flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Mic className="w-3.5 h-3.5 text-primary" />
                        <span className="text-xs font-semibold text-foreground">Giọng Thoại Đã Tách (Vocals)</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={handleToggleSoloVocals}
                          className={`px-2 py-0.5 text-[10px] font-bold rounded uppercase transition-colors ${
                            isSoloVocals
                              ? "bg-amber-500 text-white shadow-sm shadow-amber-500/30"
                              : "bg-secondary hover:bg-secondary/80 text-muted-foreground"
                          }`}
                          title="Chỉ nghe giọng thoại (tắt nhạc nền)"
                        >
                          Solo
                        </button>
                        <button
                          onClick={() => setVocalsMuted(prev => !prev)}
                          className={`p-1 rounded transition-colors ${
                            vocalsMuted
                              ? "bg-rose-500/10 text-rose-500"
                              : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                          }`}
                          title={vocalsMuted ? "Bật âm thanh giọng thoại" : "Tắt tiếng giọng thoại"}
                        >
                          {vocalsMuted ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
                        </button>
                      </div>
                    </div>

                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={vocalsMuted ? 0 : vocalsVolume}
                        onChange={(e) => {
                          const val = parseFloat(e.target.value);
                          setVocalsVolume(val);
                          if (vocalsMuted && val > 0) setVocalsMuted(false);
                        }}
                        className="w-full accent-primary bg-secondary h-1.5 rounded-lg cursor-pointer"
                      />
                      <span className={`text-[10px] font-mono font-bold w-10 text-right ${vocalsMuted ? "text-rose-500" : "text-muted-foreground"}`}>
                        {vocalsMuted ? "MUTE" : `${Math.round(vocalsVolume * 100)}%`}
                      </span>
                    </div>
                  </div>

                  {/* Track 2: BGM */}
                  <div className="p-2.5 rounded-lg bg-background/60 border border-border/50 flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Music className="w-3.5 h-3.5 text-accent" />
                        <span className="text-xs font-semibold text-foreground">Nhạc Nền / Hiệu Ứng (BGM)</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <button
                          onClick={handleToggleSoloBgm}
                          className={`px-2 py-0.5 text-[10px] font-bold rounded uppercase transition-colors ${
                            isSoloBgm
                              ? "bg-amber-500 text-white shadow-sm shadow-amber-500/30"
                              : "bg-secondary hover:bg-secondary/80 text-muted-foreground"
                          }`}
                          title="Chỉ nghe nhạc nền (tắt giọng thoại)"
                        >
                          Solo
                        </button>
                        <button
                          onClick={() => setBgmMuted(prev => !prev)}
                          className={`p-1 rounded transition-colors ${
                            bgmMuted
                              ? "bg-rose-500/10 text-rose-500"
                              : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                          }`}
                          title={bgmMuted ? "Bật âm thanh nhạc nền" : "Tắt tiếng nhạc nền"}
                        >
                          {bgmMuted ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
                        </button>
                      </div>
                    </div>

                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        value={bgmMuted ? 0 : bgmVolume}
                        onChange={(e) => {
                          const val = parseFloat(e.target.value);
                          setBgmVolume(val);
                          if (bgmMuted && val > 0) setBgmMuted(false);
                        }}
                        className="w-full accent-accent bg-secondary h-1.5 rounded-lg cursor-pointer"
                      />
                      <span className={`text-[10px] font-mono font-bold w-10 text-right ${bgmMuted ? "text-rose-500" : "text-muted-foreground"}`}>
                        {bgmMuted ? "MUTE" : `${Math.round(bgmVolume * 100)}%`}
                      </span>
                    </div>
                  </div>
                </div>

                <p className="text-[10px] text-muted-foreground/80 leading-relaxed italic bg-secondary/20 p-2 rounded-lg border border-border/40">
                  Giọng nói và nhạc nền đã được cách ly thành 2 track độc lập. Bạn có thể bấm <strong>Solo</strong> để thẩm âm từng track hoặc kéo thanh trượt để tinh chỉnh tỷ lệ hòa trộn trước khi hoàn tất lồng tiếng.
                </p>
              </div>

              {renderVoiceSelector(true)}

              <button
                onClick={handleFinalize}
                disabled={loading}
                className="w-full py-3 bg-primary hover:bg-primary/90 text-primary-foreground text-xs font-bold rounded-xl transition-all shadow-md shadow-primary/10 flex items-center justify-center gap-2 disabled:opacity-50 cursor-pointer"
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Đang hoàn tất lồng tiếng...</span>
                  </>
                ) : (
                  <>
                    <Wand2 className="w-4 h-4" />
                    <span>Xác Nhận Bản Dịch & Lồng Tiếng Video</span>
                  </>
                )}
              </button>

            </div>
          </SectionCard>

          {/* Right: Subtitle Timeline Editor */}
          <SectionCard title="Biên Tập Bản Dịch Phụ Đề">
            <div className="flex flex-col gap-3">
              {job.error_message && (
                <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-xl text-xs text-amber-600 dark:text-amber-400 flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                  <div className="flex flex-col gap-0.5">
                    <span className="font-bold">Cảnh báo dịch thuật tự động:</span>
                    <span className="text-[11px] font-mono break-all">{job.error_message}</span>
                    <span className="text-[10px] text-muted-foreground mt-0.5">
                      Hệ thống đã giữ lại các phân đoạn phụ đề để bạn tự chỉnh sửa bên dưới, hoặc bạn có thể chọn Profile LLM khác và bấm <strong>Dịch Lại Bằng AI</strong>.
                    </span>
                  </div>
                </div>
              )}

              <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
                <div className="flex items-center gap-1.5">
                  {llmProfiles.length > 0 && (
                    <select
                      value={selectedLlmProfileId || ""}
                      onChange={(e) => setSelectedLlmProfileId(e.target.value)}
                      className="bg-background border border-border rounded-lg px-2 py-1 text-[11px] text-foreground font-medium focus:ring-1 focus:ring-primary outline-hidden"
                    >
                      {llmProfiles.map(p => (
                        <option key={p.id} value={p.id}>{p.name} ({p.model})</option>
                      ))}
                    </select>
                  )}
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        setRetranslating(true);
                        await api.updateDubbingSubtitles(job.id, originalSubs, translatedSubs);
                        const updated = await api.retryDubbingTranslation(job.id, {
                          llm_profile_id: selectedLlmProfileId || undefined,
                          target_language: targetLanguage || job.target_language
                        });
                        setJob(updated);
                        if (updated.original_subtitles) setOriginalSubs(updated.original_subtitles);
                        if (updated.translated_subtitles) setTranslatedSubs(updated.translated_subtitles);
                        alert("Đã làm sạch phụ đề gốc & dịch lại toàn bộ qua AI thành công!");
                      } catch (e: unknown) {
                        alert(`Lỗi khi dịch lại: ${getErrorMessage(e, "Mô hình LLM không phản hồi")}`);
                      } finally {
                        setRetranslating(false);
                      }
                    }}
                    disabled={retranslating}
                    className="px-2.5 py-1 bg-secondary hover:bg-secondary/80 text-foreground text-xs font-semibold rounded-lg border border-border transition-colors cursor-pointer flex items-center gap-1"
                    title="Làm sạch ảo giác ASR và dịch lại tất cả phân đoạn bằng mô hình AI"
                  >
                    <RefreshCw className={`w-3 h-3 ${retranslating ? "animate-spin text-primary" : ""}`} />
                    <span>{retranslating ? "Đang dịch..." : "Dịch Lại Bằng AI"}</span>
                  </button>

                  <button
                    type="button"
                    onClick={handleCleanAsrHallucinations}
                    className="px-2.5 py-1 bg-amber-500/10 hover:bg-amber-500/20 text-amber-600 dark:text-amber-400 text-xs font-semibold rounded-lg border border-amber-500/30 transition-colors cursor-pointer flex items-center gap-1"
                    title="Tự động cắt bỏ các câu/phân đoạn bị Whisper ASR bịa thêm ở cuối video hoặc chứa ký tự lỗi"
                  >
                    <Sparkles className="w-3 h-3" />
                    <span>Lọc Rác ASR</span>
                  </button>
                </div>

                <button
                  onClick={handleSaveSubtitles}
                  disabled={savingSubs}
                  className="px-3 py-1 bg-secondary hover:bg-secondary/80 text-foreground text-xs font-semibold rounded-lg border border-border transition-colors cursor-pointer flex items-center gap-1.5"
                >
                  <Save className="w-3.5 h-3.5 text-primary" />
                  <span>{savingSubs ? "Đang lưu..." : "Lưu phụ đề"}</span>
                </button>
              </div>

              <div className="flex flex-col h-[450px] overflow-y-auto pr-1 gap-3">
                {translatedSubs.map((seg, idx) => {
                  const origSeg = originalSubs[idx] || seg;
                  const isSelected = selectedSegId === seg.id;
                  const isPlayingNow = activeOrigSeg?.id === seg.id;
                  const isEditingOrig = editingOrigSegId === seg.id;
                  const duration = typeof seg.start === "number" && typeof seg.end === "number" ? Math.max(0.1, seg.end - seg.start) : 2.0;
                  const nextSeg = translatedSubs[idx + 1];
                  const gapAfter = nextSeg && typeof nextSeg.start === "number" && typeof seg.end === "number"
                    ? Math.max(0, nextSeg.start - seg.end)
                    : 4.0;
                  const safeBorrow = Math.min(0.80, Math.max(0, gapAfter - 0.30) * 0.42);
                  const allowedDur = duration + safeBorrow;
                  const { syllables, estSeconds } = estimateDubbingSpokenStats(seg.text || "", targetLanguage || job?.target_language);
                  const isOverDuration = estSeconds > allowedDur * 1.06;
                  const isUnderDuration = duration >= 2.8 && estSeconds < duration * 0.78;
                  const isShortening = shorteningSegId === seg.id;

                  return (
                    <div
                      key={seg.id}
                      onClick={() => jumpToSegment(seg.start, seg.id)}
                      className={`p-3.5 rounded-xl border transition-all cursor-pointer ${
                        isPlayingNow
                          ? "bg-amber-500/10 border-amber-500 shadow-sm ring-1 ring-amber-500/30"
                          : isSelected
                          ? "bg-primary/10 border-primary shadow-sm"
                          : isOverDuration
                          ? "bg-amber-500/5 border-amber-500/40 hover:border-amber-500/60"
                          : "bg-secondary/20 border-border/60 hover:border-border"
                      }`}
                    >
                      <div className="flex flex-wrap justify-between items-center gap-2 mb-2">
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-bold text-primary tracking-wider">PHÂN ĐOẠN #{seg.id}</span>
                          {isPlayingNow && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-amber-500 text-white flex items-center gap-1 animate-pulse">
                              <span>● Đang phát</span>
                            </span>
                          )}
                          {isOverDuration ? (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30 flex items-center gap-1">
                              <AlertTriangle className="w-2.5 h-2.5" />
                              <span>Quá dài (~{estSeconds}s / tối đa {allowedDur.toFixed(1)}s)</span>
                            </span>
                          ) : isUnderDuration ? (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-sky-500/15 text-sky-600 dark:text-sky-400 border border-sky-500/30 flex items-center gap-1">
                              <span>Hơi ngắn (~{estSeconds}s / {duration.toFixed(1)}s)</span>
                            </span>
                          ) : (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25 flex items-center gap-1">
                              <span>Khớp nhịp (~{estSeconds}s / {duration.toFixed(1)}s)</span>
                            </span>
                          )}
                        </div>

                        <div className="flex items-center gap-1.5">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleShortenSubtitle(seg.id, seg.text, duration, origSeg.text);
                            }}
                            disabled={isShortening}
                            className={`px-2 py-0.5 text-[10px] font-bold rounded border transition-all flex items-center gap-1 cursor-pointer ${
                              isOverDuration
                                ? "bg-amber-500 text-white hover:bg-amber-600 border-amber-600 shadow-xs"
                                : "bg-primary/10 text-primary hover:bg-primary/20 border-primary/30"
                            }`}
                            title="Dùng AI rút gọn câu này để vừa khớp số giây nói"
                          >
                            {isShortening ? (
                              <>
                                <Loader2 className="w-3 h-3 animate-spin" />
                                <span>Đang rút gọn...</span>
                              </>
                            ) : (
                              <>
                                <Sparkles className="w-3 h-3" />
                                <span>Rút gọn AI</span>
                              </>
                            )}
                          </button>

                          <span className="text-[10px] font-mono font-bold text-muted-foreground bg-background px-2 py-0.5 rounded border border-border">
                            {typeof seg.start === "number" ? seg.start.toFixed(2) : "0.00"}s → {typeof seg.end === "number" ? seg.end.toFixed(2) : "0.00"}s ({duration.toFixed(1)}s)
                          </span>

                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteSegment(seg.id);
                            }}
                            className="p-1 rounded hover:bg-destructive/15 text-muted-foreground hover:text-destructive transition-colors cursor-pointer"
                            title="Xóa phân đoạn này (nếu ASR nhận diện dư / bịa lời ở cuối video)"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                      
                      <div className="flex flex-col gap-2">
                        <div>
                          <div className="flex items-center justify-between">
                            <span className="text-[9px] uppercase font-bold text-amber-600 dark:text-amber-400 tracking-wider">
                              Gốc (Tiếng Anh • ASR):
                            </span>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setEditingOrigSegId(isEditingOrig ? null : seg.id);
                              }}
                              className="text-[9px] font-semibold text-muted-foreground hover:text-primary flex items-center gap-1 cursor-pointer"
                            >
                              <Edit3 className="w-2.5 h-2.5" />
                              <span>{isEditingOrig ? "Xong" : "Sửa câu gốc ASR"}</span>
                            </button>
                          </div>
                          {isEditingOrig ? (
                            <textarea
                              value={origSeg.text}
                              onChange={(e) => updateOrigSubText(origSeg.id, e.target.value)}
                              onClick={(e) => e.stopPropagation()}
                              rows={2}
                              className="w-full bg-background border border-amber-500/50 rounded-lg p-2 text-xs text-foreground mt-1 focus:outline-none focus:border-amber-500"
                            />
                          ) : (
                            <p
                              onClick={(e) => {
                                e.stopPropagation();
                                setEditingOrigSegId(seg.id);
                              }}
                              title="Bấm để sửa văn bản Gốc (ASR) nếu nhận diện sai hoặc thừa chữ"
                              className="text-xs text-foreground/85 italic mt-0.5 hover:bg-background/60 rounded px-1.5 py-0.5 -mx-1.5 transition-colors"
                            >
                              {origSeg.text}
                            </p>
                          )}
                        </div>
                        
                        <div>
                          <div className="flex items-center justify-between">
                            <span className="text-[9px] uppercase font-bold text-primary tracking-wider">Dịch ({targetLanguage}):</span>
                            <span className="text-[9px] text-muted-foreground font-mono">
                              {syllables} âm tiết • ước tính ~{estSeconds}s
                            </span>
                          </div>
                          <textarea
                            value={seg.text}
                            onChange={(e) => updateSubText(seg.id, e.target.value)}
                            onClick={(e) => e.stopPropagation()}
                            rows={2}
                            className={`w-full bg-background border rounded-lg p-2 text-xs text-foreground mt-1 focus:outline-none focus:border-primary font-medium ${
                              isOverDuration ? "border-amber-500/50" : "border-border"
                            }`}
                          />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </SectionCard>

        </div>
        );
      })()}

      {/* --- STEP 5: FINAL OUTPUT PREVIEW & DOWNLOAD --- */}
      {viewMode === "workspace" && jobId && job && job.id === jobId && job.status === "completed" && (
        <SectionCard title="Hoàn Tất Lồng Tiếng Video!">
          <div className="flex flex-col items-center gap-6 text-center max-w-3xl mx-auto py-4">
            <div className="w-12 h-12 rounded-full bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-500">
              <CheckCircle2 className="w-6 h-6" />
            </div>

            <div>
              <h2 className="text-lg font-bold text-foreground">Video Đã Được Lồng Tiếng Thành Công!</h2>
              <p className="text-xs text-muted-foreground mt-1">
                Giọng nói đã được tổng hợp clone chính xác và khớp thời lượng từng phân đoạn.
              </p>
            </div>

            <div className="aspect-video w-full max-w-2xl rounded-2xl overflow-hidden bg-black border border-border shadow-xl">
              <video
                key={`dubbed_video_${jobId}_${outputVideoTimestamp}`}
                src={api.getDubbingFileUrl(jobId, "output", false, false, outputVideoTimestamp)}
                controls
                playsInline
                preload="metadata"
                className="w-full h-full object-contain"
              />
            </div>

            <div className="flex flex-wrap gap-3 justify-center">
              <a
                href={api.getDubbingFileUrl(jobId, "output", true)}
                download={`dubbed_video_${jobId}.mp4`}
                target="_blank"
                rel="noopener noreferrer"
                className="px-4 py-2.5 bg-gradient-to-r from-primary to-accent hover:brightness-105 text-white font-bold text-xs rounded-xl transition-all shadow-md flex items-center gap-2 cursor-pointer"
              >
                <Download className="w-4 h-4" />
                <span>Tải Video MP4 Lồng Tiếng</span>
              </a>
              
              <button
                onClick={() => {
                  const srtText = originalSubs.map((seg, idx) => {
                    const trans = translatedSubs[idx] || seg;
                    const format = (s: number) => {
                      const h = Math.floor(s / 3600);
                      const m = Math.floor((s % 3600) / 60);
                      const sec = Math.floor(s % 60);
                      const ms = Math.floor((s % 1) * 1000);
                      return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')},${String(ms).padStart(3, '0')}`;
                    };
                    return `${idx + 1}\n${format(seg.start)} --> ${format(seg.end)}\n${trans.text}\n`;
                  }).join("\n");
                  
                  const blob = new Blob([srtText], { type: "text/plain;charset=utf-8" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = `subtitles_${jobId}.srt`;
                  document.body.appendChild(a);
                  a.click();
                  document.body.removeChild(a);
                }}
                className="px-4 py-2.5 bg-secondary hover:bg-secondary/80 text-foreground font-bold text-xs rounded-xl border border-border transition-colors flex items-center gap-2 cursor-pointer"
              >
                <FileText className="w-4 h-4" />
                <span>Tải Phụ Đề SRT</span>
              </button>

              {/* Action 1: Re-edit Subtitles & Re-dub */}
              <button
                onClick={handleReEditJob}
                disabled={reEditing}
                className="px-4 py-2.5 bg-amber-500 hover:bg-amber-600 text-white font-bold text-xs rounded-xl transition-all shadow-md shadow-amber-500/20 flex items-center gap-2 cursor-pointer disabled:opacity-50"
                title="Quay lại Step 3 để chỉnh sửa câu từ, rút gọn câu hoặc đổi giọng đọc"
              >
                {reEditing ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Đang mở lại...</span>
                  </>
                ) : (
                  <>
                    <Edit3 className="w-4 h-4" />
                    <span>Chỉnh Sửa Phụ Đề & Lồng Tiếng Lại</span>
                  </>
                )}
              </button>

              {/* Action 2: Toggle Fast Remix Panel */}
              <button
                onClick={() => setShowRemixPanel(prev => !prev)}
                className={`px-4 py-2.5 text-xs font-bold rounded-xl border transition-all flex items-center gap-2 cursor-pointer ${
                  showRemixPanel
                    ? "bg-primary text-primary-foreground border-primary shadow-sm"
                    : "bg-secondary hover:bg-secondary/80 text-foreground border-border"
                }`}
                title="Cân chỉnh âm lượng giọng nói và nhạc nền mà không cần tạo lại giọng"
              >
                <Sliders className="w-4 h-4 text-primary" />
                <span>{showRemixPanel ? "Đóng Bảng Hòa Âm" : "Hòa Âm Lại Nhanh (3s)"}</span>
              </button>
            </div>

            {/* FAST REMIX & AUTO-DUCKING PANEL */}
            {showRemixPanel && (
              <div className="w-full max-w-2xl bg-secondary/30 border border-primary/30 rounded-2xl p-5 flex flex-col gap-4 text-left animate-in fade-in zoom-in-95 duration-200">
                <div className="flex items-center justify-between border-b border-border/60 pb-3">
                  <div className="flex items-center gap-2.5">
                    <div className="p-2 bg-primary/10 text-primary rounded-xl">
                      <Sliders className="w-4 h-4" />
                    </div>
                    <div>
                      <h4 className="text-xs font-bold text-foreground">Hòa Âm Lại Siêu Tốc & Auto-Ducking</h4>
                      <p className="text-[11px] text-muted-foreground">Xuất lại video với cân bằng âm thanh mới trong 3 giây (không cần sinh lại TTS)</p>
                    </div>
                  </div>
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
                    Sidechain Auto-Ducking Active
                  </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* Vocal Volume Slider */}
                  <div className="p-3 bg-background rounded-xl border border-border/60 flex flex-col gap-2">
                    <div className="flex items-center justify-between text-xs font-bold">
                      <span className="flex items-center gap-1.5 text-foreground">
                        <Mic className="w-3.5 h-3.5 text-primary" />
                        <span>Giọng Lồng Tiếng</span>
                      </span>
                      <span className="font-mono text-primary">{Math.round(remixVocalVol * 100)}%</span>
                    </div>
                    <input
                      type="range"
                      min="0.2"
                      max="2.0"
                      step="0.05"
                      value={remixVocalVol}
                      onChange={(e) => setRemixVocalVol(parseFloat(e.target.value))}
                      className="w-full accent-primary bg-secondary h-1.5 rounded-lg cursor-pointer"
                    />
                    <span className="text-[10px] text-muted-foreground">Khuyên dùng: 100% - 130% để giọng rõ ràng</span>
                  </div>

                  {/* BGM Volume Slider */}
                  <div className="p-3 bg-background rounded-xl border border-border/60 flex flex-col gap-2">
                    <div className="flex items-center justify-between text-xs font-bold">
                      <span className="flex items-center gap-1.5 text-foreground">
                        <Music className="w-3.5 h-3.5 text-accent" />
                        <span>Nhạc Nền / Gốc (BGM)</span>
                      </span>
                      <span className={`font-mono ${remixBgmVol <= 0.02 ? "text-rose-500" : "text-accent"}`}>
                        {remixBgmVol <= 0.02 ? "TẮT (0%)" : `${Math.round(remixBgmVol * 100)}%`}
                      </span>
                    </div>
                    <input
                      type="range"
                      min="0"
                      max="1.0"
                      step="0.05"
                      value={remixBgmVol}
                      onChange={(e) => setRemixBgmVol(parseFloat(e.target.value))}
                      className="w-full accent-accent bg-secondary h-1.5 rounded-lg cursor-pointer"
                    />
                    <span className="text-[10px] text-muted-foreground">Kéo về 0% để tắt sạch 100% âm thanh gốc</span>
                  </div>
                </div>

                <div className="flex items-center justify-between pt-1">
                  <span className="text-[11px] text-muted-foreground italic">
                    Nhạc nền sẽ tự động giảm nhỏ (-14dB) khi giọng nói cất lên và hồi phục khi hết câu.
                  </span>
                  <button
                    onClick={handleFastRemix}
                    disabled={remixing}
                    className="px-4 py-2 bg-primary hover:bg-primary/90 text-primary-foreground font-bold text-xs rounded-xl shadow-md transition-all flex items-center gap-2 cursor-pointer disabled:opacity-50"
                  >
                    {remixing ? (
                      <>
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        <span>Đang hòa âm lại...</span>
                      </>
                    ) : (
                      <>
                        <Zap className="w-3.5 h-3.5" />
                        <span>Áp Dụng Hòa Âm Mới (3s)</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            )}

          </div>
        </SectionCard>
      )}

      {/* --- YOUTUBE AUTHENTICATION MODAL --- */}
      {isAdmin && showCookieModal && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="dubbing-auth-modal-title"
        >
          <div className="bg-card border border-border rounded-2xl max-w-lg w-full p-6 shadow-2xl flex flex-col gap-4 animate-in fade-in zoom-in-95 duration-200">
            <div className="flex items-center justify-between pb-3 border-b border-border">
              <div className="flex items-center gap-2.5">
                <div className="p-2 bg-primary/10 text-primary rounded-xl">
                  <Key className="w-5 h-5" />
                </div>
                <div>
                  <h3 id="dubbing-auth-modal-title" className="text-sm font-bold text-foreground">Kết Nối Tài Khoản YouTube</h3>
                  <p className="text-[11px] text-muted-foreground">Mở khóa tải video YouTube Full HD 1080p 60fps siêu tốc</p>
                </div>
              </div>
              <button
                type="button"
                aria-label="Đóng hộp thoại"
                onClick={() => { setShowCookieModal(false); setCookieMsg(null); }}
                className="p-2 min-w-[36px] min-h-[36px] flex items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Windows Tunnel Bridge & R2 Staging Status */}
            {bridgeStatus?.configured && (
              <div className={`p-3 rounded-xl border flex flex-col gap-1.5 text-xs ${
                bridgeStatus.bridge_online
                  ? "bg-emerald-500/10 border-emerald-500/30"
                  : "bg-amber-500/10 border-amber-500/30"
              }`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-bold text-foreground flex items-center gap-1.5">
                    <span className={`w-2 h-2 rounded-full ${bridgeStatus.bridge_online ? "bg-emerald-500 animate-pulse" : "bg-amber-500"}`} />
                    <span>Windows Tunnel Bridge + R2 Cache</span>
                  </span>
                  <div className="flex items-center gap-1.5 text-[10px] font-bold">
                    <span className={`px-2 py-0.5 rounded-full ${
                      bridgeStatus.bridge_online
                        ? "bg-emerald-500/20 text-emerald-700 dark:text-emerald-300"
                        : "bg-amber-500/20 text-amber-700 dark:text-amber-300"
                    }`}>
                      {bridgeStatus.bridge_online ? "Tunnel: Online" : "Tunnel: Offline"}
                    </span>
                    <span className={`px-2 py-0.5 rounded-full ${
                      bridgeStatus.r2_online
                        ? "bg-emerald-500/20 text-emerald-700 dark:text-emerald-300"
                        : "bg-destructive/20 text-destructive"
                    }`}>
                      {bridgeStatus.r2_online ? "R2: Online" : "R2: Offline"}
                    </span>
                  </div>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  {bridgeStatus.bridge_detail} • {bridgeStatus.r2_detail}
                </p>
              </div>
            )}

            {/* Modal Tabs */}
            <div className="flex items-center gap-2 p-1 bg-secondary/50 rounded-xl border border-border">
              <button
                type="button"
                onClick={() => setActiveModalTab("proxy")}
                className={`flex-1 py-1.5 text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-1.5 cursor-pointer ${
                  activeModalTab === "proxy"
                    ? "bg-primary text-primary-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Globe className="w-3.5 h-3.5" />
                <span>Proxy Dân Cư (Khuyên Dùng)</span>
                {proxyPool.length > 0 && (
                  <span className={`px-1.5 py-0.2 text-[10px] font-bold rounded-full ${
                    activeModalTab === "proxy" ? "bg-white/20 text-white" : "bg-purple-500/20 text-purple-600 dark:text-purple-400"
                  }`}>
                    {proxyPool.length}
                  </span>
                )}
              </button>
              <button
                type="button"
                onClick={() => setActiveModalTab("oauth")}
                className={`flex-1 py-1.5 text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-1.5 cursor-pointer ${
                  activeModalTab === "oauth"
                    ? "bg-primary text-primary-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Key className="w-3.5 h-3.5" />
                <span>Đăng Nhập 1-Click</span>
              </button>
              <button
                type="button"
                onClick={() => setActiveModalTab("cookies")}
                className={`flex-1 py-1.5 text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-1.5 cursor-pointer ${
                  activeModalTab === "cookies"
                    ? "bg-primary text-primary-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Cookie className="w-3.5 h-3.5" />
                <span>Dán Cookies.txt</span>
              </button>
            </div>

            {/* TAB 1: PROXY POOL (PRIMARY) */}
            {activeModalTab === "proxy" && (
              <div className="flex flex-col gap-3.5">
                {/* Header status card */}
                {proxyPool.length > 0 ? (
                  <div className="p-3 bg-purple-500/10 border border-purple-500/30 rounded-xl flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      <div className="p-2 bg-purple-500/20 text-purple-600 dark:text-purple-400 rounded-lg">
                        <Globe className="w-4 h-4" />
                      </div>
                      <div>
                        <h4 className="text-xs font-bold text-foreground flex items-center gap-1.5">
                          <span>Proxy Pool: {proxyPool.length} Proxy Sẵn Sàng</span>
                          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                        </h4>
                        <p className="text-[11px] text-muted-foreground">Tự động xoay vòng qua từng proxy khi tải video.</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={handleTestAllProxies}
                        disabled={proxyTestingAll}
                        className="px-2.5 py-1.5 bg-secondary hover:bg-secondary/80 text-foreground text-xs font-bold rounded-lg border border-border transition-colors flex items-center gap-1 cursor-pointer disabled:opacity-50"
                        title="Kiểm tra kết nối và độ trễ của tất cả proxy"
                      >
                        {proxyTestingAll ? (
                          <Loader2 className="w-3 h-3 animate-spin" />
                        ) : (
                          <RefreshCw className="w-3 h-3" />
                        )}
                        <span>{proxyTestingAll ? "Đang test..." : "Test Tất Cả"}</span>
                      </button>
                      <button
                        type="button"
                        onClick={handleClearProxyPool}
                        className="p-1.5 bg-destructive/10 hover:bg-destructive/20 text-destructive rounded-lg border border-destructive/30 transition-colors cursor-pointer"
                        title="Xóa toàn bộ proxy trong pool"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="p-3.5 bg-secondary/40 border border-border rounded-xl">
                    <h4 className="text-xs font-bold text-foreground mb-1">Hệ Thống Proxy Dân Cư Xoay Vòng (Proxy Pool)</h4>
                    <p className="text-[11px] text-muted-foreground leading-relaxed">
                      Dán danh sách Proxy dân cư (từ Webshare hoặc nhà mạng). Khi tải video, hệ thống tự động xoay vòng qua từng proxy còn sống để bypass hoàn toàn lỗi chặn bot của YouTube.
                    </p>
                  </div>
                )}

                {/* Proxy List items */}
                {proxyPool.length > 0 && (
                  <div className="flex flex-col gap-1.5 max-h-48 overflow-y-auto pr-1">
                    {proxyPool.map((p, idx) => {
                      const testRes = proxyTestResults[p.hash];
                      return (
                        <div
                          key={p.hash}
                          className="p-2.5 bg-background border border-border rounded-xl flex items-center justify-between text-xs transition-all hover:border-border/80"
                        >
                          <div className="flex flex-col gap-0.5 min-w-0 pr-2">
                            <div className="flex items-center gap-1.5">
                              <span className="px-1.5 py-0.5 bg-secondary text-[10px] font-mono font-bold rounded uppercase text-muted-foreground">
                                #{idx + 1} {p.scheme}
                              </span>
                              <span className="font-mono text-xs font-medium text-foreground truncate">
                                {p.masked}
                              </span>
                            </div>
                            {testRes && (
                              <div className="flex items-center gap-1.5 text-[10px]">
                                {testRes.status === "online" ? (
                                  <>
                                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />
                                    <span className="text-emerald-600 dark:text-emerald-400 font-bold">
                                      Online ({testRes.latency_ms}ms)
                                    </span>
                                    <span className="text-muted-foreground">
                                      • IP: {testRes.ip} [{testRes.country || "N/A"}] {testRes.city ? `(${testRes.city})` : ""}
                                    </span>
                                  </>
                                ) : (
                                  <>
                                    <span className="w-1.5 h-1.5 rounded-full bg-destructive shrink-0" />
                                    <span className="text-destructive font-medium truncate">
                                      {testRes.message}
                                    </span>
                                  </>
                                )}
                              </div>
                            )}
                          </div>
                          <button
                            type="button"
                            onClick={() => handleDeleteProxyItem(p.hash)}
                            className="p-1.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-lg transition-colors shrink-0 cursor-pointer"
                            title="Xóa proxy này"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Batch Add Section */}
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-bold text-foreground">
                      Thêm Proxy Vào Danh Sách (Dán hàng loạt):
                    </label>
                    <span className="text-[10px] text-muted-foreground">Mỗi dòng 1 proxy</span>
                  </div>
                  <textarea
                    rows={3}
                    placeholder={`http://username:password@p.webshare.io:80\nsocks5://user:pass@12.34.56.78:1080\nhttp://username:password@p.webshare.io:81`}
                    value={proxyBatchInput}
                    onChange={(e) => {
                      setProxyBatchInput(e.target.value);
                      setProxyPoolMsg(null);
                    }}
                    className="bg-background border border-border rounded-xl p-3 text-xs font-mono text-foreground focus:outline-none focus:border-primary resize-none placeholder:text-muted-foreground/50"
                  />
                  <p className="text-[10px] text-muted-foreground">
                    Hỗ trợ định dạng: <code className="text-primary font-mono">http://user:pass@host:port</code>, <code className="text-primary font-mono">socks5://user:pass@host:port</code> hoặc <code className="text-primary font-mono">host:port:user:pass</code> (Webshare)
                  </p>
                </div>

                {/* Feedback Message */}
                {proxyPoolMsg && (
                  <div className="p-2.5 bg-primary/10 border border-primary/20 text-primary text-xs rounded-xl text-center font-bold">
                    {proxyPoolMsg}
                  </div>
                )}

                {/* Add button */}
                <button
                  type="button"
                  onClick={handleAddProxyPool}
                  disabled={proxyAdding || !proxyBatchInput.trim()}
                  className="w-full py-2.5 bg-primary hover:bg-primary/90 text-primary-foreground font-bold text-xs rounded-xl shadow-md transition-all flex items-center justify-center gap-1.5 disabled:opacity-50 cursor-pointer"
                >
                  {proxyAdding ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      <span>Đang lưu vào pool...</span>
                    </>
                  ) : (
                    <>
                      <Save className="w-3.5 h-3.5" />
                      <span>Thêm Vào Proxy Pool</span>
                    </>
                  )}
                </button>

                {/* Help tip */}
                <div className="p-3 bg-secondary/20 rounded-xl border border-border/60 text-[11px] text-muted-foreground flex flex-col gap-1">
                  <span className="font-bold text-foreground">Nơi lấy Proxy miễn phí / giá rẻ:</span>
                  <span>• Bạn có thể đăng ký tài khoản tại <a href="https://www.webshare.io" target="_blank" rel="noreferrer" className="text-primary underline font-bold">Webshare.io</a> để nhận ngay <strong>10 Proxy miễn phí</strong> vĩnh viễn (hỗ trợ HTTP & SOCKS5).</span>
                  <span>• Trong Webshare, bấm <strong>Download List</strong> hoặc <strong>Copy All</strong> rồi dán trực tiếp vào ô trên.</span>
                </div>
              </div>
            )}

            {/* TAB 2: 1-CLICK OAUTH */}
            {activeModalTab === "oauth" && (
              <div className="flex flex-col gap-3">
                {/* Connected State */}
                {oauthStatus?.connected ? (
                  <div className="p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-xl flex flex-col gap-3">
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 rounded-full">
                        <ShieldCheck className="w-6 h-6" />
                      </div>
                      <div className="flex-grow">
                        <h4 className="text-xs font-bold text-foreground">Đã Kết Nối Tài Khoản YouTube (OAuth 2.0)</h4>
                        <p className="text-[11px] text-muted-foreground">Server đã được cấp quyền tải mọi video Full HD tốc độ cao mà không bị chặn bot.</p>
                      </div>
                    </div>
                    <div className="flex justify-end">
                      <button
                        type="button"
                        onClick={handleDisconnectOAuth}
                        className="px-3 py-1.5 bg-destructive/10 hover:bg-destructive/20 text-destructive text-xs font-bold rounded-lg transition-colors flex items-center gap-1.5 cursor-pointer"
                      >
                        <LogOut className="w-3.5 h-3.5" />
                        <span>Ngắt Kết Nối</span>
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {!oauthFlowData ? (
                      <div className="flex flex-col gap-3 p-4 bg-secondary/30 border border-border rounded-xl">
                        <div className="flex items-start gap-2.5">
                          <Sparkles className="w-5 h-5 text-primary shrink-0 mt-0.5" />
                          <div className="text-xs text-muted-foreground leading-relaxed">
                            <b className="text-foreground">Không cần xuất file cookie thủ công!</b> Chỉ cần bấm nút bên dưới, mở trang xác nhận của Google và nhập mã gồm 8 ký tự để hoàn tất kết nối trong 3 giây.
                          </div>
                        </div>

                        {oauthError && (
                          <div className="p-2.5 bg-destructive/10 border border-destructive/30 rounded-lg text-xs text-destructive font-medium">
                            {oauthError}
                          </div>
                        )}

                        <button
                          type="button"
                          onClick={handleStartOAuth}
                          disabled={oauthStarting}
                          className="w-full py-2.5 bg-primary hover:bg-primary/90 text-primary-foreground text-xs font-bold rounded-xl shadow-md transition-all flex items-center justify-center gap-2 disabled:opacity-50 cursor-pointer"
                        >
                          {oauthStarting ? (
                            <>
                              <Loader2 className="w-4 h-4 animate-spin" />
                              <span>Đang khởi tạo mã xác thực...</span>
                            </>
                          ) : (
                            <>
                              <Key className="w-4 h-4" />
                              <span>Lấy Mã Đăng Nhập Google</span>
                            </>
                          )}
                        </button>
                      </div>
                    ) : (
                      <div className="flex flex-col gap-3.5 p-4 bg-primary/5 border border-primary/20 rounded-xl">
                        <div className="flex flex-col gap-1 text-center">
                          <span className="text-xs text-muted-foreground">Bước 1: Copy mã xác thực bên dưới</span>
                          <div className="flex items-center justify-center gap-2 py-2">
                            <span className="font-mono text-xl font-extrabold tracking-widest text-primary bg-primary/10 px-4 py-1.5 rounded-lg border border-primary/20 select-all">
                              {oauthFlowData.user_code}
                            </span>
                            <button
                              type="button"
                              onClick={handleCopyOAuthCode}
                              className="p-2 bg-secondary hover:bg-secondary/80 text-foreground rounded-lg border border-border transition-colors cursor-pointer"
                              title="Sao chép mã"
                            >
                              {oauthCopied ? <Check className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
                            </button>
                          </div>
                        </div>

                        <div className="flex flex-col gap-1 text-center">
                          <span className="text-xs text-muted-foreground">Bước 2: Mở trang xác thực của Google và dán mã vào:</span>
                          <a
                            href={oauthFlowData.verification_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center justify-center gap-1.5 py-2 px-4 bg-primary hover:bg-primary/90 text-primary-foreground font-bold text-xs rounded-xl shadow transition-all"
                          >
                            <span>Mở Trang Xác Thực Google</span>
                            <ExternalLink className="w-3.5 h-3.5" />
                          </a>
                        </div>

                        {oauthPolling && (
                          <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground pt-1">
                            <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
                            <span>Đang chờ bạn xác nhận trên trình duyệt...</span>
                          </div>
                        )}

                        {oauthSuccessMsg && (
                          <div className="p-2.5 bg-emerald-500/10 border border-emerald-500/30 rounded-lg text-xs text-emerald-600 dark:text-emerald-400 font-bold text-center">
                            {oauthSuccessMsg}
                          </div>
                        )}

                        {oauthError && (
                          <div className="p-2.5 bg-destructive/10 border border-destructive/30 rounded-lg text-xs text-destructive font-medium text-center">
                            {oauthError}
                          </div>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {/* TAB 3: COOKIES */}
            {activeModalTab === "cookies" && (
              <div className="flex flex-col gap-3">
                <div className="flex items-center justify-between p-3 bg-secondary/40 rounded-xl border border-border gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                      cookieStatus?.is_authenticated
                        ? "bg-emerald-500"
                        : cookieStatus?.has_cookies
                        ? "bg-blue-500"
                        : "bg-amber-500"
                    }`} />
                    <span className="text-xs font-semibold text-foreground truncate">
                      {cookieStatus?.has_cookies
                        ? cookieStatus?.is_authenticated
                          ? `Cookie đã đăng nhập YouTube (${cookieStatus.cookie_count ?? 0} cookies)`
                          : `Cookie ẩn danh (${cookieStatus.cookie_count ?? 0} cookies) — nên dán Cookie đã đăng nhập`
                        : "Trạng thái Cookie: Chưa có Cookie"}
                    </span>
                  </div>
                  {cookieStatus?.has_cookies && (
                    <button
                      type="button"
                      onClick={handleDeleteCookie}
                      className="text-xs text-destructive hover:underline flex items-center gap-1 cursor-pointer shrink-0"
                    >
                      <Trash2 className="w-3 h-3" />
                      <span>Xóa Cookie</span>
                    </button>
                  )}
                </div>

                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-bold text-foreground">
                      Dán nội dung Netscape Cookies.txt:
                    </label>
                    <label className="text-xs text-primary hover:underline cursor-pointer flex items-center gap-1">
                      <Upload className="w-3 h-3" />
                      <span>Hoặc tải file .txt</span>
                      <input
                        type="file"
                        accept=".txt"
                        className="hidden"
                        onChange={handleUploadCookieFile}
                      />
                    </label>
                  </div>
                  <textarea
                    rows={6}
                    placeholder="# Netscape HTTP Cookie File&#10;.youtube.com	TRUE	/	TRUE	...	SID	..."
                    value={cookieText}
                    onChange={(e) => setCookieText(e.target.value)}
                    className="bg-background border border-border rounded-xl p-3 text-xs font-mono text-foreground focus:outline-none focus:border-primary resize-none placeholder:text-muted-foreground/50"
                  />
                </div>

                {cookieMsg && (
                  <div className="p-2.5 bg-primary/10 border border-primary/20 text-primary text-xs rounded-xl text-center font-bold">
                    {cookieMsg}
                  </div>
                )}

                <div className="flex justify-end gap-2 pt-1">
                  <button
                    type="button"
                    onClick={handleSaveCookie}
                    disabled={cookieUploading || !cookieText.trim()}
                    className="px-4 py-2 bg-primary hover:brightness-105 text-primary-foreground text-xs font-bold rounded-xl transition-all disabled:opacity-50 flex items-center gap-1.5 cursor-pointer"
                  >
                    <Save className="w-3.5 h-3.5" />
                    <span>{cookieUploading ? "Đang lưu..." : "Lưu Nội Dung Cookie"}</span>
                  </button>
                </div>
              </div>
            )}


            {ytDiagResult && (
              <div className={`p-2.5 rounded-xl border text-xs font-medium ${
                ytDiagResult.status === "success"
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-600 dark:text-emerald-400"
                  : "bg-destructive/10 border-destructive/30 text-destructive"
              }`}>
                {ytDiagResult.message}
              </div>
            )}

            <div className="flex items-center justify-between gap-2 pt-2 border-t border-border">
              <button
                type="button"
                onClick={() => void handleTestYouTubeConnection()}
                disabled={ytDiagTesting}
                className="px-4 py-2 bg-primary/10 hover:bg-primary/20 text-primary font-bold text-xs rounded-xl border border-primary/25 transition-colors flex items-center gap-1.5 disabled:opacity-50 cursor-pointer"
              >
                {ytDiagTesting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
                <span>{ytDiagTesting ? "Đang kiểm tra..." : "Kiểm Tra Tải YouTube"}</span>
              </button>
              <button
                type="button"
                onClick={() => { setShowCookieModal(false); setCookieMsg(null); setYtDiagResult(null); }}
                className="px-5 py-2 bg-secondary hover:bg-secondary/80 text-foreground font-bold text-xs rounded-xl border border-border transition-colors cursor-pointer"
              >
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
