import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Play,
  Pause,
  Loader2,
  Sparkles,
  Settings2,
  CheckCircle2,
  Mic,
  Sliders,
  Wand2,
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  Download,
  RotateCcw,
  RefreshCw,
  FileText,
  Clock,
  Zap,
} from "lucide-react";
import { api, getErrorMessage } from "../api/client";
import type { JobStatusResponse, VoiceSampleResponse } from "../api/client";

import { JobStatusCard } from "./JobStatusCard";
import { PageHeader } from "./ui/PageHeader";
import { SectionCard } from "./ui/SectionCard";

interface TTSPanelProps {
  activeVoiceSampleId: string | null;
  onSelectVoiceSample?: (sampleId: string | null) => void;
  onJobCreatedOrUpdated?: () => void;
  layout?: "classic" | "modern";
  currentPlayUrl: string | null;
  globalPlayerPlaying: boolean;
  onPlayAudio: (url: string, title: string) => void;
  onTogglePlay: () => void;
}

const DEFAULT_SAMPLE_TEXT =
  "Học sinh hôm nay được nghỉ học do thời tiết xấu. Xin nhắc lại, học sinh được nghỉ học.";

function loadRawInputMap(): Record<string, string> {
  try {
    const raw = localStorage.getItem("VITE_TTS_RAW_INPUT_MAP");
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, string>;
    }
  } catch {
    // ignore storage parse error
  }
  return {};
}

function saveRawInputForJob(jobId: string, rawText: string) {
  try {
    const current = loadRawInputMap();
    const entries = Object.entries({ ...current, [jobId]: rawText });
    const trimmed = Object.fromEntries(entries.slice(-20));
    localStorage.setItem("VITE_TTS_RAW_INPUT_MAP", JSON.stringify(trimmed));
  } catch {
    // ignore quota error
  }
}

function isTerminalStatus(st?: string | null): boolean {
  return st === "completed" || st === "failed" || st === "cancelled";
}

function getModeLabel(jobType?: string): { label: string; className: string } {
  switch (jobType) {
    case "clone_voice":
      return { label: "Nhân bản giọng", className: "bg-primary/10 text-primary border-primary/25" };
    case "voice_design_tts":
    case "voice_design":
      return { label: "Thiết kế giọng", className: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 border-indigo-500/25" };
    case "auto_voice":
      return { label: "Giọng tự động", className: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/25" };
    default:
      return { label: "TTS", className: "bg-secondary text-muted-foreground border-border" };
  }
}

export const TTSPanel: React.FC<TTSPanelProps> = ({
  activeVoiceSampleId,
  onSelectVoiceSample,
  onJobCreatedOrUpdated,
  currentPlayUrl,
  globalPlayerPlaying,
  onPlayAudio,
  onTogglePlay,
}) => {
  const [mode, setMode] = useState<"clone_voice" | "auto_voice" | "voice_design">("clone_voice");
  const [text, setText] = useState<string>(() => {
    const saved = localStorage.getItem("VITE_TTS_DRAFT_TEXT");
    return saved !== null ? saved : DEFAULT_SAMPLE_TEXT;
  });
  const [customVoiceSampleId, setCustomVoiceSampleId] = useState(() => activeVoiceSampleId || "");
  const [refText, setRefText] = useState("");
  const [instruct, setInstruct] = useState("female, young adult, low pitch");
  const [assistingInstruct, setAssistingInstruct] = useState(false);
  const [instructHint, setInstructHint] = useState<string | null>(null);

  const [loading, setLoading] = useState(false);
  const [jobId, setJobId] = useState<string | null>(() => localStorage.getItem("VITE_TTS_JOB_ID"));
  const [jobStatus, setJobStatus] = useState<JobStatusResponse | null>(null);
  const [isPolling, setIsPolling] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [rawInputMap, setRawInputMap] = useState<Record<string, string>>(() => loadRawInputMap());
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [downloadingJobId, setDownloadingJobId] = useState<string | null>(null);

  // Live recent TTS jobs feed on the same page
  const [recentJobs, setRecentJobs] = useState<JobStatusResponse[]>([]);
  const [loadingRecent, setLoadingRecent] = useState<boolean>(true);
  const [expandedRecentIds, setExpandedRecentIds] = useState<Record<string, boolean>>({});

  const [speed, setSpeed] = useState(1.0);
  const [numStep, setNumStep] = useState(32);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [denoise, setDenoise] = useState(true);
  const [guidanceScale, setGuidanceScale] = useState(2.0);
  const [tShift, setTShift] = useState<number>(0.1);

  const [positionTemperature, setPositionTemperature] = useState(5.0);
  const [classTemperature, setClassTemperature] = useState(0.0);
  const [layerPenaltyFactor, setLayerPenaltyFactor] = useState<number>(5.0);
  const [duration] = useState<number | null>(null);
  const [preprocessPrompt] = useState<boolean>(true);
  const [postprocessOutput] = useState<boolean>(true);
  const [audioChunkDuration] = useState(15.0);
  const [audioChunkThreshold] = useState(30.0);
  const [language, setLanguage] = useState("");
  const [padDuration, setPadDuration] = useState<string>("");
  const [fadeDuration, setFadeDuration] = useState<string>("");
  const [normalizeText, setNormalizeText] = useState<boolean>(true);

  const [activePreset, setActivePreset] = useState("Tự nhiên");

  const applyPreset = (presetName: string) => {
    setActivePreset(presetName);
    switch (presetName) {
      case "Tự nhiên":
        setSpeed(1.0); setNumStep(32); setGuidanceScale(2.0); setPositionTemperature(5.0); setClassTemperature(0.0); setDenoise(true); setTShift(0.1); setLayerPenaltyFactor(5.0);
        break;
      case "Kể chuyện":
        setSpeed(0.9); setNumStep(40); setGuidanceScale(2.0); setPositionTemperature(4.0); setClassTemperature(0.1); setDenoise(true); setTShift(0.1); setLayerPenaltyFactor(5.0);
        break;
      case "Quảng cáo":
        setSpeed(1.1); setNumStep(32); setGuidanceScale(2.5); setPositionTemperature(6.0); setClassTemperature(0.2); setDenoise(true); setTShift(0.15); setLayerPenaltyFactor(5.0);
        break;
      case "Tin tức":
        setSpeed(1.05); setNumStep(32); setGuidanceScale(3.0); setPositionTemperature(5.0); setClassTemperature(0.0); setDenoise(true); setTShift(0.1); setLayerPenaltyFactor(5.0);
        break;
      case "Chậm rõ":
        setSpeed(0.8); setNumStep(40); setGuidanceScale(2.5); setPositionTemperature(4.0); setClassTemperature(0.0); setDenoise(true); setTShift(0.1); setLayerPenaltyFactor(5.0);
        break;
      case "Nhanh gọn":
        setSpeed(1.3); setNumStep(20); setGuidanceScale(1.5); setPositionTemperature(6.0); setClassTemperature(0.0); setDenoise(false); setTShift(0.05); setLayerPenaltyFactor(5.0);
        break;
    }
  };

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const pollIntervalRef = useRef<number | null>(null);
  const timeoutRef = useRef<number | null>(null);
  const pollInFlightRef = useRef<boolean>(false);
  const pollErrorCountRef = useRef<number>(0);
  const jobIdRef = useRef<string | null>(jobId);

  useEffect(() => {
    jobIdRef.current = jobId;
  }, [jobId]);

  useEffect(() => {
    try {
      localStorage.setItem("VITE_TTS_DRAFT_TEXT", text);
    } catch {
      // ignore storage quota errors
    }
  }, [text]);

  const [voiceSamples, setVoiceSamples] = useState<VoiceSampleResponse[]>([]);
  const onJobUpdatedRef = useRef(onJobCreatedOrUpdated);
  useEffect(() => {
    onJobUpdatedRef.current = onJobCreatedOrUpdated;
  }, [onJobCreatedOrUpdated]);

  // Clears timers ONLY — safe to call inside useEffect cleanup without resetting React state in StrictMode!
  const clearPollTimers = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    pollInFlightRef.current = false;
  }, []);

  const finishPolling = useCallback(() => {
    clearPollTimers();
    pollErrorCountRef.current = 0;
    setIsPolling(false);
    setLoading(false);
  }, [clearPollTimers]);

  const fetchRecentJobs = useCallback(async (silent = false) => {
    if (!silent) setLoadingRecent(true);
    try {
      const res = await api.listJobs({ page: 1, page_size: 8, job_type: "tts" });
      const items = res.items || [];
      setRecentJobs(items);

      // Sync active card immediately if the active job is present in the fresh listing
      const currentActiveId = jobIdRef.current;
      if (currentActiveId) {
        const matched = items.find((it) => it.job_id === currentActiveId);
        if (matched) {
          setJobStatus(matched);
          if (isTerminalStatus(matched.status)) {
            finishPolling();
          }
        }
      }
    } catch (err) {
      if (!silent) {
        console.warn("Không thể tải danh sách TTS gần đây:", err);
      }
    } finally {
      if (!silent) setLoadingRecent(false);
    }
  }, [finishPolling]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const [samples, jobsRes] = await Promise.all([
          api.listVoiceSamples().catch(() => [] as VoiceSampleResponse[]),
          api.listJobs({ page: 1, page_size: 8, job_type: "tts" }).catch(() => null),
        ]);
        if (!active) return;
        setVoiceSamples(samples);
        if (jobsRes) {
          setRecentJobs(jobsRes.items || []);
        }
      } finally {
        if (active) setLoadingRecent(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    Promise.resolve().then(async () => {
      if (!active) return;
      if (!activeVoiceSampleId) {
        setCustomVoiceSampleId("");
        setRefText("");
        return;
      }
      setCustomVoiceSampleId(activeVoiceSampleId);
      setMode("clone_voice");
      try {
        const samples = await api.listVoiceSamples();
        if (active) {
          setVoiceSamples(samples);
          const matched = samples.find(s => s.id === activeVoiceSampleId);
          setRefText(matched?.ref_text || "");
        }
      } catch (err) {
        console.error("Lỗi cập nhật danh sách mẫu giọng:", err);
      }
    });
    return () => {
      active = false;
    };
  }, [activeVoiceSampleId]);

  const handleGenerate = useCallback(async () => {
    if (!text.trim() || loading || isPolling) return;
    if (text.length > 5000) {
      setErrorMsg("Văn bản vượt quá giới hạn 5.000 ký tự. Vui lòng rút gọn nội dung.");
      return;
    }
    const rawSubmitted = text.trim();
    clearPollTimers();
    pollErrorCountRef.current = 0;
    setLoading(true);
    setJobId(null);
    setJobStatus(null);
    setErrorMsg(null);

    const voiceSampleId = mode === "clone_voice" ? customVoiceSampleId : undefined;
    const refTextParam = mode === "clone_voice" && refText ? refText : undefined;
    const instructParam = mode === "voice_design" ? instruct.trim() || "female, young adult" : undefined;

    if (mode === "clone_voice" && !voiceSampleId) {
      setErrorMsg("Vui lòng chọn mẫu giọng để nhân bản.");
      setLoading(false);
      return;
    }

    const params = {
      denoise,
      guidance_scale: guidanceScale,
      t_shift: tShift,
      position_temperature: positionTemperature,
      class_temperature: classTemperature,
      layer_penalty_factor: layerPenaltyFactor,
      duration: duration !== null ? duration : undefined,
      preprocess_prompt: preprocessPrompt,
      postprocess_output: postprocessOutput,
      audio_chunk_duration: audioChunkDuration,
      audio_chunk_threshold: audioChunkThreshold,
      language: language || undefined,
      pad_duration: padDuration !== "" ? parseFloat(padDuration) : undefined,
      fade_duration: fadeDuration !== "" ? parseFloat(fadeDuration) : undefined,
      normalize_text: normalizeText,
    };

    try {
      const res = await api.createTTSJob(mode, rawSubmitted, voiceSampleId, instructParam, speed, numStep, params, refTextParam);
      saveRawInputForJob(res.job_id, rawSubmitted);
      setRawInputMap((prev) => ({ ...prev, [res.job_id]: rawSubmitted }));
      setJobId(res.job_id);
      jobIdRef.current = res.job_id;
      localStorage.setItem("VITE_TTS_JOB_ID", res.job_id);
      setJobStatus({
        job_id: res.job_id,
        status: res.status,
        message: res.message,
        progress: 0,
        audio_url: null,
        error_message: null,
        job_type: mode === "voice_design" ? "voice_design_tts" : mode,
        text: res.text || rawSubmitted,
        queue_time: 0,
        total_time: 0,
      });
      setIsPolling(true);
      onJobUpdatedRef.current?.();
      void fetchRecentJobs(true);
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, "Không thể khởi tạo tiến trình TTS."));
      setLoading(false);
    }
  }, [
    text,
    mode,
    customVoiceSampleId,
    refText,
    instruct,
    denoise,
    guidanceScale,
    tShift,
    positionTemperature,
    classTemperature,
    layerPenaltyFactor,
    duration,
    preprocessPrompt,
    postprocessOutput,
    audioChunkDuration,
    audioChunkThreshold,
    language,
    padDuration,
    fadeDuration,
    normalizeText,
    speed,
    numStep,
    loading,
    isPolling,
    clearPollTimers,
    fetchRecentJobs,
  ]);

  const handleGenerateRef = useRef(handleGenerate);
  useEffect(() => {
    handleGenerateRef.current = handleGenerate;
  }, [handleGenerate]);

  useEffect(() => {
    const onRegenerate = () => {
      void handleGenerateRef.current();
    };
    window.addEventListener("omnivoice:regenerate", onRegenerate);
    return () => {
      window.removeEventListener("omnivoice:regenerate", onRegenerate);
    };
  }, []);

  // Restore saved active TTS job on mount
  useEffect(() => {
    const savedJobId = localStorage.getItem("VITE_TTS_JOB_ID");
    if (!savedJobId) return;
    let active = true;
    (async () => {
      try {
        const status = await api.getJobStatus(savedJobId);
        if (!active) return;
        setJobId(savedJobId);
        setJobStatus(status);
        setLoading(false);
        if (!isTerminalStatus(status.status)) {
          setIsPolling(true);
        }
      } catch (err) {
        console.error("Lỗi khôi phục TTS job status:", err);
        if (active) {
          setJobId(null);
          setJobStatus(null);
          localStorage.removeItem("VITE_TTS_JOB_ID");
          setLoading(false);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // Fault-tolerant realtime polling for the active TTS job
  const pollActiveJobOnce = useCallback(async (targetJobId: string) => {
    if (!targetJobId || pollInFlightRef.current) return;
    pollInFlightRef.current = true;
    try {
      const status = await api.getJobStatus(targetJobId);
      if (jobIdRef.current !== targetJobId) return;
      pollErrorCountRef.current = 0;
      setJobStatus(status);
      setRecentJobs((prev) => {
        const idx = prev.findIndex((j) => j.job_id === status.job_id);
        if (idx === -1) return prev;
        const copy = [...prev];
        copy[idx] = status;
        return copy;
      });
      if (isTerminalStatus(status.status)) {
        finishPolling();
        onJobUpdatedRef.current?.();
        void fetchRecentJobs(true);
      }
    } catch (err: unknown) {
      const msg = getErrorMessage(err);
      const isNotFound = msg.includes("404") || msg.toLowerCase().includes("không tìm thấy job");
      pollErrorCountRef.current += 1;
      if (isNotFound || pollErrorCountRef.current >= 10) {
        setErrorMsg(`Lỗi khi thăm dò trạng thái: ${msg}`);
        finishPolling();
      } else {
        console.warn(`Transient TTS poll warning (${pollErrorCountRef.current}/10):`, msg);
      }
    } finally {
      pollInFlightRef.current = false;
    }
  }, [finishPolling, fetchRecentJobs]);

  useEffect(() => {
    if (!isPolling || !jobId) return;

    const initialTimer = window.setTimeout(() => {
      void pollActiveJobOnce(jobId);
    }, 50);

    pollIntervalRef.current = window.setInterval(() => {
      void pollActiveJobOnce(jobId);
    }, 1500);

    timeoutRef.current = window.setTimeout(() => {
      setErrorMsg("Hết thời gian chờ xử lý (Timeout 15 phút).");
      finishPolling();
    }, 900000);

    return () => {
      window.clearTimeout(initialTimer);
      clearPollTimers();
    };
  }, [isPolling, jobId, pollActiveJobOnce, clearPollTimers, finishPolling]);

  // Auto-poll recent TTS jobs whenever any item is in-flight (including jobs submitted via API / other tabs)
  const hasInFlightRecent = recentJobs.some((j) => !isTerminalStatus(j.status));

  useEffect(() => {
    const intervalMs = hasInFlightRecent ? 2500 : 10000;
    const timer = window.setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      void fetchRecentJobs(true);
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [hasInFlightRecent, fetchRecentJobs]);

  // Instant resync when returning to browser tab / window focus (zero F5 needed!)
  useEffect(() => {
    const resyncNow = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      const activeId = jobIdRef.current;
      if (activeId) {
        void pollActiveJobOnce(activeId);
      }
      void fetchRecentJobs(true);
    };
    window.addEventListener("focus", resyncNow);
    document.addEventListener("visibilitychange", resyncNow);
    return () => {
      window.removeEventListener("focus", resyncNow);
      document.removeEventListener("visibilitychange", resyncNow);
    };
  }, [pollActiveJobOnce, fetchRecentJobs]);

  const handleClearJob = () => {
    finishPolling();
    setJobId(null);
    jobIdRef.current = null;
    setJobStatus(null);
    localStorage.removeItem("VITE_TTS_JOB_ID");
  };

  const handleCopyText = (key: string, val: string) => {
    if (!val) return;
    navigator.clipboard.writeText(val).then(() => {
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey(null), 1800);
    }).catch(() => {});
  };

  const handleDownloadAudio = async (targetJob: JobStatusResponse) => {
    const rawUrl = targetJob.cdn_audio_url || (targetJob.audio_url ? `${api.getApiBaseUrl()}${targetJob.audio_url}` : "");
    if (!rawUrl || downloadingJobId === targetJob.job_id) return;
    setDownloadingJobId(targetJob.job_id);
    try {
      const { blobUrl, isObjectUrl } = await api.fetchProtectedAudioBlobUrl(rawUrl);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = `omnivoice_tts_${targetJob.job_id}.wav`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      if (isObjectUrl) {
        window.setTimeout(() => URL.revokeObjectURL(blobUrl), 2500);
      }
    } catch (err) {
      setErrorMsg(`Không thể tải tệp WAV: ${getErrorMessage(err)}`);
    } finally {
      setDownloadingJobId(null);
    }
  };

  const handleRestoreTextToEditor = (val: string) => {
    if (!val) return;
    setText(val);
    textareaRef.current?.focus();
  };

  const activeTtsText = (jobStatus?.text || (jobStatus ? rawInputMap[jobStatus.job_id] : "") || "").trim();
  const activeRawSubmitted = jobStatus ? (rawInputMap[jobStatus.job_id] || "").trim() : "";
  const hasNormalizedDiff =
    Boolean(activeRawSubmitted) &&
    Boolean(activeTtsText) &&
    activeRawSubmitted.replace(/\s+/g, " ") !== activeTtsText.replace(/\s+/g, " ");
  const activeAudioUrl =
    jobStatus && (jobStatus.cdn_audio_url || jobStatus.audio_url)
      ? jobStatus.cdn_audio_url || `${api.getApiBaseUrl()}${jobStatus.audio_url}`
      : "";

  return (
    <div className="w-full flex flex-col gap-6">
      <PageHeader
        title="Tạo giọng đọc"
        description="Tạo audio từ văn bản, cập nhật tiến trình thời gian thực và đối soát trực tiếp văn bản TTS."
        icon={<Sparkles className="w-6 h-6" />}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
        <div className="lg:col-span-2 flex flex-col gap-6">
          {/* Step 1: Select Mode & Voice */}
          <SectionCard title="1. Chọn phương thức &amp; giọng đọc">
            <div className="flex flex-col gap-4">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2" role="tablist" aria-label="Chế độ tạo giọng">
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "clone_voice"}
                  onClick={() => setMode("clone_voice")}
                  className={`min-h-[44px] px-3 py-2 rounded-xl border text-xs font-semibold flex items-center justify-center gap-2 cursor-pointer transition-colors ${
                    mode === "clone_voice"
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-secondary/40 text-muted-foreground hover:text-foreground border-border"
                  }`}
                >
                  <Mic className="w-3.5 h-3.5 shrink-0" />
                  <span>Nhân bản giọng</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "voice_design"}
                  onClick={() => setMode("voice_design")}
                  className={`min-h-[44px] px-3 py-2 rounded-xl border text-xs font-semibold flex items-center justify-center gap-2 cursor-pointer transition-colors ${
                    mode === "voice_design"
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-secondary/40 text-muted-foreground hover:text-foreground border-border"
                  }`}
                >
                  <Sliders className="w-3.5 h-3.5 shrink-0" />
                  <span>Thiết kế giọng (Prompt)</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mode === "auto_voice"}
                  onClick={() => setMode("auto_voice")}
                  className={`min-h-[44px] px-3 py-2 rounded-xl border text-xs font-semibold flex items-center justify-center gap-2 cursor-pointer transition-colors ${
                    mode === "auto_voice"
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-secondary/40 text-muted-foreground hover:text-foreground border-border"
                  }`}
                >
                  <Wand2 className="w-3.5 h-3.5 shrink-0" />
                  <span>Giọng tự động</span>
                </button>
              </div>

              {mode === "clone_voice" && (
                <>
                  {activeVoiceSampleId && customVoiceSampleId === activeVoiceSampleId && (
                    <div className="bg-primary/10 border border-primary/30 p-3 rounded-lg flex items-center gap-3">
                      <CheckCircle2 className="w-5 h-5 text-primary shrink-0" />
                      <div className="flex flex-col">
                        <span className="text-xs font-bold text-primary">Giọng đang chọn</span>
                        <span className="text-sm font-medium">{activeVoiceSampleId.substring(0, 24)}</span>
                      </div>
                    </div>
                  )}
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor="tts-voice-select" className="text-xs font-semibold text-foreground">
                      Mẫu giọng tham chiếu
                    </label>
                    <select
                      id="tts-voice-select"
                      value={customVoiceSampleId}
                      onChange={(e) => {
                        const val = e.target.value;
                        setCustomVoiceSampleId(val);
                        onSelectVoiceSample?.(val || null);
                        const found = voiceSamples.find(s => s.id === val);
                        setRefText(found?.ref_text || "");
                      }}
                      className="bg-secondary/50 border border-border/70 hover:border-border rounded-xl px-4 min-h-[48px] text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/20 w-full"
                    >
                      <option value="">-- Chọn một mẫu giọng --</option>
                      <optgroup label="Giọng cá nhân (Private)">
                        {voiceSamples.filter(s => !s.is_public).map(s => (
                          <option key={s.id} value={s.id}>
                            {s.name || s.id.substring(0, 10)} {s.duration ? `[${s.duration.toFixed(1)}s]` : ""}
                          </option>
                        ))}
                      </optgroup>
                      <optgroup label="Giọng cộng đồng (Public)">
                        {voiceSamples.filter(s => s.is_public).map(s => (
                          <option key={s.id} value={s.id}>
                            {s.name || s.id.substring(0, 10)} {s.duration ? `[${s.duration.toFixed(1)}s]` : ""}
                          </option>
                        ))}
                      </optgroup>
                    </select>
                  </div>
                </>
              )}

              {mode === "voice_design" && (
                <div className="flex flex-col gap-2">
                  <label htmlFor="tts-voice-instruct" className="text-xs font-semibold text-foreground">
                    Mô tả đặc điểm giọng nói (Tiếng Việt tự nhiên hoặc OmniVoice Instruct)
                  </label>
                  <div className="flex flex-col sm:flex-row gap-2">
                    <input
                      id="tts-voice-instruct"
                      type="text"
                      value={instruct}
                      onChange={(e) => {
                        setInstruct(e.target.value);
                        setInstructHint(null);
                      }}
                      placeholder="Ví dụ: Giọng nam trung niên đọc bản tin thời sự hoặc female, young adult, low pitch"
                      className="min-h-[44px] flex-1 px-3.5 py-2.5 bg-background border border-input rounded-xl text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
                    />
                    <button
                      type="button"
                      disabled={assistingInstruct || !instruct.trim()}
                      onClick={async () => {
                        if (!instruct.trim() || assistingInstruct) return;
                        setAssistingInstruct(true);
                        setErrorMsg(null);
                        try {
                          const res = await api.assistVoiceDesign({
                            voice_request: instruct.trim(),
                            target_language: language || "auto",
                            preserve_preview_text: true,
                          });
                          setInstruct(res.instruct);
                          setSpeed(res.recommended_params.speed);
                          setGuidanceScale(res.recommended_params.guidance_scale);
                          setPositionTemperature(res.recommended_params.position_temperature);
                          setClassTemperature(res.recommended_params.class_temperature);
                          setActivePreset("");
                          setInstructHint(res.explanation);
                        } catch (err: unknown) {
                          setErrorMsg(getErrorMessage(err, "Không thể tối ưu mô tả giọng bằng AI."));
                        } finally {
                          setAssistingInstruct(false);
                        }
                      }}
                      className="min-h-[44px] px-4 py-2 rounded-xl bg-secondary hover:bg-secondary/80 border border-border text-xs font-bold text-foreground flex items-center justify-center gap-1.5 cursor-pointer shrink-0 transition-colors"
                    >
                      {assistingInstruct ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Wand2 className="w-3.5 h-3.5 text-primary" />
                      )}
                      <span>AI Tối ưu</span>
                    </button>
                  </div>
                  {instructHint && (
                    <p className="text-xs text-muted-foreground bg-secondary/40 border border-border rounded-xl px-3 py-2">
                      {instructHint}
                    </p>
                  )}
                  <div className="flex flex-wrap gap-1.5">
                    {[
                      "female, young adult, low pitch",
                      "male, middle-aged, low pitch",
                      "female, young adult, whisper",
                      "male, young adult, british accent",
                    ].map((sample) => (
                      <button
                        type="button"
                        key={sample}
                        onClick={() => {
                          setInstruct(sample);
                          setInstructHint(null);
                        }}
                        className="min-h-[36px] px-2.5 py-1 rounded-lg bg-muted hover:bg-secondary border border-border text-xs font-mono text-muted-foreground hover:text-foreground cursor-pointer transition-colors"
                      >
                        {sample}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {mode === "auto_voice" && (
                <p className="text-xs text-muted-foreground bg-muted/50 border border-border rounded-xl p-3">
                  Hệ thống tự động chọn giọng đọc tự nhiên phù hợp nhất với văn bản bạn nhập, không cần mẫu ghi âm tham chiếu.
                </p>
              )}
            </div>
          </SectionCard>

          {/* Step 2: Enter Text */}
          <SectionCard title="2. Nhập văn bản">
            <label htmlFor="tts-text-input" className="sr-only">Nội dung văn bản cần tổng hợp</label>
            <textarea
              id="tts-text-input"
              ref={textareaRef}
              value={text}
              maxLength={5000}
              onChange={(e) => setText(e.target.value)}
              placeholder="Nhập nội dung cần đọc..."
              className="w-full bg-secondary/30 rounded-xl p-4 min-h-[200px] border border-border/60 focus:border-primary/50 focus:outline-none resize-y leading-relaxed"
            />
            <div className="flex items-center justify-between mt-2 text-xs text-muted-foreground">
              <span>
                {text.trim() ? `${text.trim().split(/\s+/).length} từ` : "0 từ"}
              </span>
              <span className={`font-mono ${text.length >= 5000 ? "text-destructive font-bold" : "text-muted-foreground"}`}>
                {text.length} / 5000 ký tự
              </span>
            </div>
          </SectionCard>

          {/* Step 3: Result, Progress & TTS Text Cross-Check */}
          {jobId && jobStatus && (
            <SectionCard title="3. Kết quả &amp; Đối soát văn bản TTS">
              <div className="flex flex-col gap-4">
                <JobStatusCard
                  jobId={jobStatus.job_id}
                  status={jobStatus.status}
                  message={jobStatus.message}
                  progress={jobStatus.progress}
                  errorMessage={jobStatus.error_message}
                  queueTime={jobStatus.queue_time}
                  processingTime={jobStatus.processing_time}
                  totalTime={jobStatus.total_time}
                  startedAt={jobStatus.started_at}
                />

                {/* TTS Text Cross-Check Panel */}
                {activeTtsText && (
                  <div className="bg-secondary/30 border border-border rounded-2xl p-4 flex flex-col gap-2.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="inline-flex items-center gap-1.5 text-xs font-bold text-foreground">
                          <FileText className="w-3.5 h-3.5 text-primary" />
                          <span>Văn bản TTS đối soát</span>
                        </span>
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-background border border-border text-muted-foreground">
                          {activeTtsText.split(/\s+/).filter(Boolean).length} từ • {activeTtsText.length} ký tự
                        </span>
                        {hasNormalizedDiff && (
                          <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                            Đã chuẩn hóa số / ký hiệu để đọc
                          </span>
                        )}
                        {typeof jobStatus.total_time === "number" && (
                          <span className="inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full bg-success/10 text-success border border-success/20">
                            <Zap className="w-3 h-3" />
                            <span>{jobStatus.total_time.toFixed(1)}s</span>
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => handleCopyText(`active_${jobStatus.job_id}`, activeTtsText)}
                          className="min-h-[32px] px-2.5 py-1 rounded-lg bg-card hover:bg-secondary border border-border text-[11px] font-semibold text-muted-foreground hover:text-foreground transition-colors cursor-pointer inline-flex items-center gap-1"
                        >
                          {copiedKey === `active_${jobStatus.job_id}` ? (
                            <>
                              <Check className="w-3 h-3 text-success" />
                              <span>Đã chép</span>
                            </>
                          ) : (
                            <>
                              <Copy className="w-3 h-3" />
                              <span>Sao chép text</span>
                            </>
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleRestoreTextToEditor(activeRawSubmitted || activeTtsText)}
                          className="min-h-[32px] px-2.5 py-1 rounded-lg bg-card hover:bg-secondary border border-border text-[11px] font-semibold text-muted-foreground hover:text-foreground transition-colors cursor-pointer inline-flex items-center gap-1"
                          title="Đưa nội dung này lên ô nhập văn bản ở Bước 2 để chỉnh sửa và tạo lại"
                        >
                          <RotateCcw className="w-3 h-3" />
                          <span>Đưa vào ô nhập</span>
                        </button>
                      </div>
                    </div>

                    <div className="bg-card border border-border/80 rounded-xl p-3.5 text-sm text-foreground whitespace-pre-wrap leading-relaxed max-h-60 overflow-y-auto select-text">
                      {activeTtsText}
                    </div>

                    {hasNormalizedDiff && (
                      <div className="bg-background/70 border border-border/60 rounded-xl p-3 text-xs flex flex-col gap-1">
                        <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                          Văn bản gốc trước chuẩn hóa:
                        </span>
                        <p className="text-muted-foreground whitespace-pre-wrap leading-relaxed">
                          {activeRawSubmitted}
                        </p>
                      </div>
                    )}
                  </div>
                )}

                {jobStatus.status === "completed" && activeAudioUrl && (
                  <div className="flex flex-wrap gap-3">
                    <button
                      type="button"
                      onClick={() => {
                        if (currentPlayUrl === activeAudioUrl) onTogglePlay();
                        else onPlayAudio(activeAudioUrl, activeTtsText ? `TTS: ${activeTtsText.slice(0, 42)}` : "TTS Output");
                      }}
                      className="flex-1 min-h-[44px] bg-primary text-primary-foreground hover:bg-primary-hover px-4 rounded-xl flex items-center justify-center gap-2 text-sm font-semibold transition-colors cursor-pointer"
                    >
                      {currentPlayUrl === activeAudioUrl && globalPlayerPlaying ? (
                        <><Pause className="w-4 h-4" /> Tạm dừng</>
                      ) : (
                        <><Play className="w-4 h-4" /> Nghe đối soát</>
                      )}
                    </button>

                    <button
                      type="button"
                      disabled={downloadingJobId === jobStatus.job_id}
                      onClick={() => void handleDownloadAudio(jobStatus)}
                      className="min-h-[44px] px-4 bg-card hover:bg-secondary border border-border text-foreground rounded-xl flex items-center justify-center gap-2 text-sm font-semibold transition-colors cursor-pointer disabled:opacity-55"
                    >
                      {downloadingJobId === jobStatus.job_id ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Download className="w-4 h-4" />
                      )}
                      <span>Tải WAV</span>
                    </button>

                    <button
                      type="button"
                      onClick={handleClearJob}
                      className="min-h-[44px] px-4 bg-secondary text-foreground hover:bg-secondary/80 rounded-xl text-sm font-semibold cursor-pointer"
                    >
                      Ẩn kết quả
                    </button>
                  </div>
                )}
              </div>
            </SectionCard>
          )}

          {/* Live Recent TTS Generations for Realtime Tracking & Text Verification */}
          <SectionCard title="Kết quả tạo giọng gần đây (Tự động cập nhật Realtime)">
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  Tự động đồng bộ trực tiếp cả các tác vụ tạo trên trang này lẫn qua API mà không cần nhấn F5.
                </p>
                <button
                  type="button"
                  onClick={() => void fetchRecentJobs(false)}
                  disabled={loadingRecent}
                  className="min-h-[34px] px-3 py-1 rounded-lg bg-secondary hover:bg-secondary/80 border border-border text-xs font-semibold text-foreground inline-flex items-center gap-1.5 cursor-pointer shrink-0"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${loadingRecent ? "animate-spin text-primary" : ""}`} />
                  <span>Làm mới</span>
                </button>
              </div>

              {loadingRecent && recentJobs.length === 0 ? (
                <div className="py-6 flex items-center justify-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Đang tải kết quả gần đây...</span>
                </div>
              ) : recentJobs.length === 0 ? (
                <div className="py-6 text-center text-xs text-muted-foreground border border-dashed border-border rounded-xl">
                  Chưa có kết quả tạo giọng đọc nào gần đây.
                </div>
              ) : (
                <div className="flex flex-col gap-3 max-h-[520px] overflow-y-auto pr-1">
                  {recentJobs.map((item) => {
                    const itemUrl =
                      item.cdn_audio_url ||
                      (item.audio_url ? `${api.getApiBaseUrl()}${item.audio_url}` : "");
                    const isPlayingItem = Boolean(itemUrl && currentPlayUrl === itemUrl && globalPlayerPlaying);
                    const itemText = (item.text || rawInputMap[item.job_id] || "").trim();
                    const isLong = itemText.length > 180;
                    const expanded = Boolean(expandedRecentIds[item.job_id]);
                    const shownText = isLong && !expanded ? `${itemText.slice(0, 180)}...` : itemText;
                    const modeMeta = getModeLabel(item.job_type);
                    const isRunning = !isTerminalStatus(item.status);

                    return (
                      <div
                        key={item.job_id}
                        className={`p-3.5 rounded-2xl border transition-colors flex flex-col gap-2.5 ${
                          jobId === item.job_id
                            ? "bg-primary/[0.04] border-primary/40"
                            : "bg-secondary/20 border-border hover:border-border/80"
                        }`}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${modeMeta.className}`}>
                              {modeMeta.label}
                            </span>
                            <span className="text-[11px] font-mono text-muted-foreground">
                              #{item.job_id.slice(0, 10)}
                            </span>
                            {item.created_at && (
                              <span className="text-[11px] text-muted-foreground inline-flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                {new Date(item.created_at).toLocaleTimeString("vi-VN", {
                                  hour: "2-digit",
                                  minute: "2-digit",
                                  second: "2-digit",
                                })}
                              </span>
                            )}
                            {typeof item.queue_time === "number" && (
                              <span
                                className="text-[10px] font-mono text-muted-foreground bg-background border border-border px-2 py-0.5 rounded-full"
                                title="Thời gian khởi động / chờ GPU Worker nhận tác vụ"
                              >
                                Worker: {item.queue_time.toFixed(1)}s
                              </span>
                            )}
                            {typeof item.processing_time === "number" && (
                              <span
                                className="text-[10px] font-mono font-bold text-primary bg-primary/10 border border-primary/20 px-2 py-0.5 rounded-full inline-flex items-center gap-1"
                                title="Thời gian GPU tổng hợp (gen) ra tệp âm thanh"
                              >
                                <Zap className="w-2.5 h-2.5" />
                                Gen: {item.processing_time.toFixed(1)}s
                              </span>
                            )}
                            {typeof item.total_time === "number" && (
                              <span className="text-[10px] font-mono text-muted-foreground">
                                (Tổng {item.total_time.toFixed(1)}s)
                              </span>
                            )}
                          </div>

                          <div className="flex items-center gap-1.5">
                            {item.status === "completed" && (
                              <span className="text-[10px] font-bold px-2.5 py-0.5 rounded-full bg-success/10 text-success border border-success/20">
                                Hoàn tất
                              </span>
                            )}
                            {item.status === "failed" && (
                              <span
                                className="text-[10px] font-bold px-2.5 py-0.5 rounded-full bg-destructive/10 text-destructive border border-destructive/20"
                                title={item.error_message || ""}
                              >
                                Thất bại
                              </span>
                            )}
                            {isRunning && (
                              <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2.5 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/25">
                                <Loader2 className="w-3 h-3 animate-spin" />
                                <span>Đang xử lý ({item.progress ?? 0}%)</span>
                              </span>
                            )}
                          </div>
                        </div>

                        {/* TTS Text for instant cross-checking */}
                        {itemText ? (
                          <div className="bg-card border border-border/70 rounded-xl p-3 text-xs text-foreground leading-relaxed whitespace-pre-wrap">
                            <span>{shownText}</span>
                            {isLong && (
                              <button
                                type="button"
                                onClick={() =>
                                  setExpandedRecentIds((prev) => ({
                                    ...prev,
                                    [item.job_id]: !expanded,
                                  }))
                                }
                                className="ml-1.5 text-[11px] font-bold text-primary hover:underline cursor-pointer"
                              >
                                {expanded ? "Thu gọn" : "Xem đầy đủ"}
                              </button>
                            )}
                          </div>
                        ) : (
                          <p className="text-xs italic text-muted-foreground">
                            (Không có nội dung văn bản đính kèm)
                          </p>
                        )}

                        {isRunning && item.message && (
                          <p className="text-[11px] text-muted-foreground">
                            • {item.message}
                          </p>
                        )}
                        {item.status === "failed" && item.error_message && (
                          <p className="text-[11px] text-destructive font-mono break-all">
                            {item.error_message}
                          </p>
                        )}

                        <div className="flex flex-wrap items-center gap-2 pt-0.5">
                          {item.status === "completed" && itemUrl && (
                            <>
                              <button
                                type="button"
                                onClick={() => {
                                  if (currentPlayUrl === itemUrl) onTogglePlay();
                                  else onPlayAudio(itemUrl, itemText ? `TTS: ${itemText.slice(0, 42)}` : `TTS #${item.job_id.slice(0, 8)}`);
                                }}
                                className={`min-h-[36px] px-3.5 py-1 rounded-full text-xs font-bold inline-flex items-center gap-1.5 cursor-pointer border transition-colors ${
                                  isPlayingItem
                                    ? "bg-primary text-primary-foreground border-primary"
                                    : "bg-card hover:bg-secondary text-foreground border-border"
                                }`}
                              >
                                {isPlayingItem ? (
                                  <>
                                    <Pause className="w-3.5 h-3.5 fill-current" />
                                    <span>Tạm dừng</span>
                                  </>
                                ) : (
                                  <>
                                    <Play className="w-3.5 h-3.5 fill-current" />
                                    <span>Nghe đối soát</span>
                                  </>
                                )}
                              </button>

                              <button
                                type="button"
                                disabled={downloadingJobId === item.job_id}
                                onClick={() => void handleDownloadAudio(item)}
                                className="min-h-[36px] px-3 py-1 rounded-full bg-card hover:bg-secondary border border-border text-xs font-semibold text-foreground inline-flex items-center gap-1.5 cursor-pointer"
                              >
                                {downloadingJobId === item.job_id ? (
                                  <Loader2 className="w-3 h-3 animate-spin" />
                                ) : (
                                  <Download className="w-3 h-3" />
                                )}
                                <span>Tải WAV</span>
                              </button>
                            </>
                          )}

                          {itemText && (
                            <>
                              <button
                                type="button"
                                onClick={() => handleCopyText(`rec_${item.job_id}`, itemText)}
                                className="min-h-[36px] px-3 py-1 rounded-full bg-card hover:bg-secondary border border-border text-xs font-semibold text-muted-foreground hover:text-foreground inline-flex items-center gap-1 cursor-pointer"
                              >
                                {copiedKey === `rec_${item.job_id}` ? (
                                  <>
                                    <Check className="w-3 h-3 text-success" />
                                    <span>Đã chép</span>
                                  </>
                                ) : (
                                  <>
                                    <Copy className="w-3 h-3" />
                                    <span>Chép text</span>
                                  </>
                                )}
                              </button>

                              <button
                                type="button"
                                onClick={() => handleRestoreTextToEditor(rawInputMap[item.job_id] || itemText)}
                                className="min-h-[36px] px-3 py-1 rounded-full bg-secondary/70 hover:bg-secondary border border-border text-xs font-semibold text-muted-foreground hover:text-foreground inline-flex items-center gap-1 cursor-pointer ml-auto"
                              >
                                <RotateCcw className="w-3 h-3" />
                                <span>Nạp vào ô nhập</span>
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </SectionCard>
        </div>

        {/* Right column: Config & Generate */}
        <div className="lg:col-span-1 flex flex-col gap-6">
          <button
            type="button"
            onClick={handleGenerate}
            disabled={loading || isPolling || !text.trim() || text.length > 5000 || (mode === "clone_voice" && !customVoiceSampleId)}
            className={`w-full min-h-[52px] rounded-xl font-bold text-base transition-colors flex items-center justify-center gap-2 cursor-pointer active:translate-y-[1px] ${
              !loading && !isPolling && text.trim() && text.length <= 5000 && (mode !== "clone_voice" || customVoiceSampleId)
                ? "bg-primary text-primary-foreground hover:bg-primary-hover"
                : "bg-secondary text-muted-foreground cursor-not-allowed"
            }`}
          >
            {loading || isPolling ? <Loader2 className="w-5 h-5 animate-spin" /> : <Play className="w-5 h-5 fill-current" />}
            <span>{loading || isPolling ? "Đang xử lý..." : "Tạo Audio"}</span>
          </button>

          {errorMsg && (
            <div className="p-3 bg-destructive/10 border border-destructive/20 text-destructive rounded-xl text-sm font-semibold">
              {errorMsg}
            </div>
          )}

          <SectionCard title="Cấu hình giọng đọc">
            <div className="flex flex-col gap-5">
              <div>
                <label className="text-xs font-bold text-muted-foreground uppercase mb-2 block">Cảm xúc &amp; Nhịp điệu (Presets)</label>
                <div className="flex flex-wrap gap-2">
                  {["Tự nhiên", "Kể chuyện", "Quảng cáo", "Tin tức", "Chậm rõ", "Nhanh gọn"].map(p => (
                    <button
                      type="button"
                      key={p}
                      onClick={() => applyPreset(p)}
                      className={`px-3 py-1.5 text-xs font-semibold rounded-lg border transition-colors cursor-pointer ${
                        activePreset === p ? "bg-primary/10 border-primary text-primary" : "bg-secondary/40 border-border hover:border-primary/50 text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <button
                  type="button"
                  onClick={() => setShowAdvanced(!showAdvanced)}
                  className="min-h-[40px] flex items-center justify-between gap-2 text-xs font-bold text-muted-foreground uppercase hover:text-foreground w-full text-left cursor-pointer"
                >
                  <span className="flex items-center gap-2">
                    <Settings2 className="w-4 h-4" />
                    <span>Tham số kỹ thuật (Advanced)</span>
                  </span>
                  {showAdvanced ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                </button>

                {showAdvanced && (
                  <div className="flex flex-col gap-4 mt-4 p-4 bg-secondary/20 rounded-xl border border-border/50">
                    <label className="flex items-center gap-2 text-xs font-semibold text-muted-foreground cursor-pointer">
                      <input type="checkbox" checked={denoise} onChange={e => setDenoise(e.target.checked)} className="rounded border-border text-primary focus:ring-primary" />
                      Lọc nhiễu (Denoise)
                    </label>
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase">Tốc độ (Speed): {speed.toFixed(1)}x</span>
                      <input type="range" min="0.5" max="2.0" step="0.1" value={speed} onChange={e => setSpeed(parseFloat(e.target.value))} className="w-full" />
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase">Steps: {numStep}</span>
                      <input type="range" min="10" max="64" step="1" value={numStep} onChange={e => setNumStep(parseInt(e.target.value))} className="w-full" />
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase">Guidance Scale: {guidanceScale.toFixed(1)}</span>
                      <input type="range" min="0.5" max="5.0" step="0.1" value={guidanceScale} onChange={e => setGuidanceScale(parseFloat(e.target.value))} className="w-full" />
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase">t_shift: {tShift.toFixed(2)}</span>
                      <input type="range" min="0.01" max="0.50" step="0.01" value={tShift} onChange={e => setTShift(parseFloat(e.target.value))} className="w-full" />
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase">Position Temp: {positionTemperature.toFixed(1)}</span>
                      <input type="range" min="0.0" max="10.0" step="0.5" value={positionTemperature} onChange={e => setPositionTemperature(parseFloat(e.target.value))} className="w-full" />
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase">Layer Penalty: {layerPenaltyFactor.toFixed(1)}</span>
                      <input type="range" min="0.0" max="10.0" step="0.5" value={layerPenaltyFactor} onChange={e => setLayerPenaltyFactor(parseFloat(e.target.value))} className="w-full" />
                    </div>
                    <div className="flex flex-col gap-1">
                      <span className="text-[10px] font-bold text-muted-foreground uppercase">Ngôn ngữ (Language)</span>
                      <select
                        value={language}
                        onChange={e => setLanguage(e.target.value)}
                        className="w-full bg-background border border-border rounded-lg text-xs p-2 focus:outline-none focus:ring-1 focus:ring-primary text-foreground"
                      >
                        <option value="">Tự động nhận diện (Mặc định)</option>
                        <option value="vi">Tiếng Việt (vi)</option>
                        <option value="en">Tiếng Anh (en)</option>
                        <option value="zh">Tiếng Trung (zh)</option>
                        <option value="ja">Tiếng Nhật (ja)</option>
                        <option value="ko">Tiếng Hàn (ko)</option>
                        <option value="fr">Tiếng Pháp (fr)</option>
                        <option value="de">Tiếng Đức (de)</option>
                        <option value="es">Tiếng Tây Ban Nha (es)</option>
                      </select>
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                      <div className="flex flex-col gap-1">
                        <span className="text-[10px] font-bold text-muted-foreground uppercase">Pad Duration (Độ đệm)</span>
                        <input
                          type="number" min="0" max="2.0" step="0.05" placeholder="Mặc định" value={padDuration}
                          onChange={e => setPadDuration(e.target.value)}
                          className="bg-background border border-border rounded-lg text-xs p-2 focus:outline-none focus:ring-1 focus:ring-primary text-foreground w-full"
                        />
                      </div>
                      <div className="flex flex-col gap-1">
                        <span className="text-[10px] font-bold text-muted-foreground uppercase">Fade Duration (Làm mượt)</span>
                        <input
                          type="number" min="0" max="1.0" step="0.05" placeholder="Mặc định" value={fadeDuration}
                          onChange={e => setFadeDuration(e.target.value)}
                          className="bg-background border border-border rounded-lg text-xs p-2 focus:outline-none focus:ring-1 focus:ring-primary text-foreground w-full"
                        />
                      </div>
                    </div>

                    <div className="flex items-center justify-between p-2.5 bg-background border border-border rounded-xl">
                      <div className="flex flex-col pr-2">
                        <span className="text-xs font-semibold text-foreground">Chuẩn hóa văn bản (Normalize Text)</span>
                        <span className="text-[10px] text-muted-foreground">Tự động đọc chuẩn số (1.6, 1.600, 1,3456), phần trăm, ngày tháng, tiền tệ thành chữ đọc</span>
                      </div>
                      <input
                        type="checkbox"
                        checked={normalizeText}
                        onChange={e => setNormalizeText(e.target.checked)}
                        className="w-4 h-4 rounded border-border text-primary focus:ring-primary cursor-pointer accent-primary"
                      />
                    </div>
                  </div>
                )}
              </div>
            </div>
          </SectionCard>
        </div>
      </div>
    </div>
  );
};
