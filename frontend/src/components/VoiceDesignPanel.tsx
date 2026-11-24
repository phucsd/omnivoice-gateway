import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  Sliders,
  Wand2,
  Sparkles,
  Check,
  Heart,
  Lock,
  Globe,
  X,
  Play,
  Pause,
  Cpu,
  Copy,
  RotateCcw,
  Trash2,
  Settings2,
  ChevronDown,
  ChevronUp,
  ArrowRight,
  Loader2,
  Plus,
  Pencil,
} from "lucide-react";
import { api, getErrorMessage } from "../api/client";
import type {
  JobStatusResponse,
  VoiceDesignAttributes,
  VoiceDesignAssistResponse,
  VoiceDesignLLMStatusResponse,
  VoiceDesignPreviewHistoryItem,
  VoiceDesignVariant,
} from "../api/client";
import { JobStatusCard } from "./JobStatusCard";
import { SectionCard } from "./ui/SectionCard";

interface VoiceDesignPanelProps {
  onAcceptSuccess: (voiceSampleId: string) => void;
  onUseVoiceInTTS?: (voiceSampleId: string) => void;
  onJobCreatedOrUpdated?: () => void;
  layout?: "classic" | "modern";
  currentPlayUrl: string | null;
  globalPlayerPlaying: boolean;
  onPlayAudio: (url: string, title: string) => void;
  onTogglePlay: () => void;
}

const PERSONA_TEMPLATES: { label: string; prompt: string; lang: string }[] = [
  {
    label: "Nữ trẻ kể chuyện đêm khuya",
    prompt: "Giọng nữ trẻ miền Bắc, trầm ấm, sâu lắng, nhịp chậm rãi truyền cảm để kể chuyện đêm khuya và sách nói",
    lang: "vi",
  },
  {
    label: "Phát thanh viên thời sự",
    prompt: "Giọng nam trung niên đĩnh đạc, rõ chữ, chuẩn mực, uy tín để dẫn bản tin thời sự và tài chính",
    lang: "vi",
  },
  {
    label: "Review sản phẩm sôi nổi",
    prompt: "Giọng nữ trẻ trung, cao sáng, giàu năng lượng, bắt tai ở 3 giây đầu cho video review TikTok và quảng cáo",
    lang: "vi",
  },
  {
    label: "Ông lão kể tích xưa",
    prompt: "Giọng nam cao tuổi trầm khàn, điềm đạm, từng trải, kể chuyện lịch sử và cổ tích",
    lang: "vi",
  },
  {
    label: "Podcast tâm lý chữa lành",
    prompt: "Giọng nữ thanh niên tự nhiên, gần gũi như đang trò chuyện tâm tình trong podcast cuối tuần",
    lang: "vi",
  },
  {
    label: "British Documentary",
    prompt: "Mature male narrator with a refined British accent, low pitch, calm and authoritative documentary style",
    lang: "en",
  },
];

const GENDER_OPTIONS = [
  { value: "female", label: "Nữ (female)" },
  { value: "male", label: "Nam (male)" },
];

const AGE_OPTIONS = [
  { value: "child", label: "Thiếu nhi" },
  { value: "teenager", label: "Thiếu niên" },
  { value: "young adult", label: "Thanh niên" },
  { value: "middle-aged", label: "Trung niên" },
  { value: "elderly", label: "Cao tuổi" },
];

const PITCH_OPTIONS = [
  { value: "very low pitch", label: "Rất trầm" },
  { value: "low pitch", label: "Trầm" },
  { value: "moderate pitch", label: "Cân bằng" },
  { value: "high pitch", label: "Cao sáng" },
  { value: "very high pitch", label: "Rất cao" },
];

const STYLE_OPTIONS = [
  { value: "", label: "Tự nhiên" },
  { value: "whisper", label: "Thì thầm / ASMR" },
];

const ACCENT_GROUPS = [
  {
    label: "English Accents (Áp dụng khi đọc tiếng Anh)",
    options: [
      { value: "american accent", label: "American Accent (Mỹ)" },
      { value: "british accent", label: "British Accent (Anh)" },
      { value: "australian accent", label: "Australian Accent (Úc)" },
      { value: "canadian accent", label: "Canadian Accent (Canada)" },
      { value: "indian accent", label: "Indian Accent (Ấn Độ)" },
      { value: "japanese accent", label: "Japanese Accent" },
      { value: "korean accent", label: "Korean Accent" },
      { value: "chinese accent", label: "Chinese Accent" },
      { value: "portuguese accent", label: "Portuguese Accent" },
      { value: "russian accent", label: "Russian Accent" },
    ],
  },
  {
    label: "Chinese Dialects (Áp dụng khi đọc tiếng Trung)",
    options: [
      { value: "东北话", label: "Đông Bắc (东北话)" },
      { value: "四川话", label: "Tứ Xuyên (四川话)" },
      { value: "河南话", label: "Hà Nam (河南话)" },
      { value: "陕西话", label: "Thiểm Tây (陕西话)" },
      { value: "云南话", label: "Vân Nam (云南话)" },
      { value: "青岛话", label: "Thanh Đảo (青岛话)" },
    ],
  },
];

const PARALINGUISTIC_CUES = [
  { tag: "[laughter]", label: "Cười [laughter]" },
  { tag: "[sigh]", label: "Thở dài [sigh]" },
  { tag: "[surprise-ah]", label: "Ngạc nhiên [surprise-ah]" },
  { tag: "[question-ah]", label: "Nghi vấn [question-ah]" },
  { tag: "[confirmation-en]", label: "Đồng tình [confirmation-en]" },
];

const SCRIPT_TOPICS = [
  "Kể chuyện đêm khuya",
  "Bản tin công nghệ",
  "Quảng cáo thương hiệu",
  "Podcast tâm sự",
  "Hội thoại tự nhiên",
];

const LIBRARY_PRESET_TAGS = [
  "Miền Bắc",
  "Miền Nam",
  "Miền Trung",
  "Trẻ",
  "Trung niên",
  "Cao tuổi",
  "Kể chuyện",
  "Quảng cáo",
  "Tin tức",
  "Podcast",
  "Audiobook",
];

function buildInstructFromAttrs(attrs: VoiceDesignAttributes): string {
  const parts: string[] = [];
  if (attrs.gender) parts.push(attrs.gender);
  if (attrs.age) parts.push(attrs.age);
  if (attrs.pitch) parts.push(attrs.pitch);
  if (attrs.style) parts.push(attrs.style);
  if (attrs.accent) parts.push(attrs.accent);
  return parts.length > 0 ? parts.join(", ") : "female, young adult";
}

function parseInstructToAttrs(raw: string): VoiceDesignAttributes {
  const tokens = raw
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const next: VoiceDesignAttributes = {
    gender: null,
    age: null,
    pitch: null,
    style: null,
    accent: null,
  };
  const allAccents = ACCENT_GROUPS.flatMap((g) => g.options.map((o) => o.value));
  for (const tok of tokens) {
    const low = tok.toLowerCase();
    if (!next.gender && (low === "female" || low === "male")) {
      next.gender = low;
    } else if (!next.age && AGE_OPTIONS.some((o) => o.value === low)) {
      next.age = low;
    } else if (!next.pitch && PITCH_OPTIONS.some((o) => o.value === low)) {
      next.pitch = low;
    } else if (!next.style && low === "whisper") {
      next.style = "whisper";
    } else if (!next.accent) {
      const matchedAcc = allAccents.find((a) => a.toLowerCase() === low || a === tok);
      if (matchedAcc) next.accent = matchedAcc;
    }
  }
  return next;
}

function estimatePreviewStats(text: string, lang: string, speed: number): { words: number; seconds: number } {
  const cleaned = (text || "").replace(/\[[^[\]]{1,40}\]/g, " ").trim();
  if (!cleaned) return { words: 0, seconds: 0 };
  const safeSpeed = Math.max(0.5, Math.min(2.0, speed || 1.0));
  if (lang === "zh" || lang === "ja" || lang === "ko") {
    const chars = cleaned.replace(/\s+/g, "").length;
    return { words: chars, seconds: Number(((chars / 4.2) / safeSpeed).toFixed(1)) };
  }
  const words = cleaned.split(/\s+/).filter(Boolean).length;
  const rate = lang === "en" ? 2.7 : 3.9;
  return { words, seconds: Number(((words / rate) / safeSpeed).toFixed(1)) };
}

export const VoiceDesignPanel: React.FC<VoiceDesignPanelProps> = ({
  onAcceptSuccess,
  onUseVoiceInTTS,
  onJobCreatedOrUpdated,
  currentPlayUrl,
  globalPlayerPlaying,
  onPlayAudio,
  onTogglePlay,
}) => {
  // Natural prompt & AI Architect state
  const [voiceRequest, setVoiceRequest] = useState(
    "Giọng nữ trẻ miền Bắc, trầm ấm, truyền cảm để kể chuyện đêm khuya và sách nói"
  );
  const [llmStatus, setLlmStatus] = useState<VoiceDesignLLMStatusResponse | null>(null);
  const [selectedLlmProfileId, setSelectedLlmProfileId] = useState<string>("");
  const [autoRewriteScript, setAutoRewriteScript] = useState<boolean>(true);
  const [assisting, setAssisting] = useState<boolean>(false);
  const [assistResult, setAssistResult] = useState<VoiceDesignAssistResponse | null>(null);
  const [selectedVariantIdx, setSelectedVariantIdx] = useState<number>(0);

  // Structured OmniVoice Attributes & Instruct state
  const [attributes, setAttributes] = useState<VoiceDesignAttributes>({
    gender: "female",
    age: "young adult",
    pitch: "low pitch",
    style: null,
    accent: null,
  });
  const [instructText, setInstructText] = useState<string>("female, young adult, low pitch");
  const [manualInstructMode, setManualInstructMode] = useState<boolean>(false);
  const [copiedInstruct, setCopiedInstruct] = useState<boolean>(false);

  // Script & Language state
  const [language, setLanguage] = useState<string>("vi");
  const [previewText, setPreviewText] = useState<string>(
    "Đêm dần buông xuống bên hiên nhà cổ, tiếng gió khẽ lùa qua tán lá như gợi lại câu chuyện xưa đầy kỷ niệm êm đềm một thời đã xa."
  );
  const [scriptTopic, setScriptTopic] = useState<string>("Kể chuyện đêm khuya");
  const [generatingScript, setGeneratingScript] = useState<boolean>(false);
  const scriptTextareaRef = useRef<HTMLTextAreaElement>(null);

  // Acoustic & Prosody Parameters state
  const [activePreset, setActivePreset] = useState<string>("Kể chuyện");
  const [aiTunedBadge, setAiTunedBadge] = useState<boolean>(false);
  const [speed, setSpeed] = useState<number>(0.92);
  const [numStep, setNumStep] = useState<number>(36);
  const [guidanceScale, setGuidanceScale] = useState<number>(2.2);
  const [positionTemperature, setPositionTemperature] = useState<number>(4.4);
  const [classTemperature, setClassTemperature] = useState<number>(0.08);
  const [tShift, setTShift] = useState<number>(0.10);
  const [layerPenaltyFactor, setLayerPenaltyFactor] = useState<number>(5.0);
  const [denoise, setDenoise] = useState<boolean>(true);
  const [preprocessPrompt, setPreprocessPrompt] = useState<boolean>(true);
  const [postprocessOutput, setPostprocessOutput] = useState<boolean>(true);
  const [normalizeText, setNormalizeText] = useState<boolean>(true);
  const [duration, setDuration] = useState<string>("");
  const [padDuration, setPadDuration] = useState<string>("");
  const [fadeDuration, setFadeDuration] = useState<string>("");
  const [showAdvanced, setShowAdvanced] = useState<boolean>(false);

  // Preview Job & History state
  const [loading, setLoading] = useState<boolean>(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [resolvedInstruct, setResolvedInstruct] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<JobStatusResponse | null>(null);
  const [isPolling, setIsPolling] = useState<boolean>(false);
  const [accepting, setAccepting] = useState<boolean>(false);
  const [lastSavedVoiceId, setLastSavedVoiceId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const [recentPreviews, setRecentPreviews] = useState<VoiceDesignPreviewHistoryItem[]>([]);
  const [loadingHistory, setLoadingHistory] = useState<boolean>(true);
  const [deletingPreviewId, setDeletingPreviewId] = useState<string | null>(null);

  // Save Voice Modal state (with AI suggested metadata)
  const [suggestedMeta, setSuggestedMeta] = useState<{
    name: string;
    slug: string;
    tags: string[];
  }>({
    name: "",
    slug: "",
    tags: ["Trẻ", "Kể chuyện"],
  });
  const [isSaveModalOpen, setIsSaveModalOpen] = useState<boolean>(false);
  const [saveTargetPreviewId, setSaveTargetPreviewId] = useState<string | null>(null);
  const [saveVoiceName, setSaveVoiceName] = useState<string>("");
  const [saveVoiceRefText, setSaveVoiceRefText] = useState<string>("");
  const [saveVoiceCustomId, setSaveVoiceCustomId] = useState<string>("");
  const [saveVoiceTags, setSaveVoiceTags] = useState<string[]>([]);
  const [customTagInput, setCustomTagInput] = useState<string>("");
  const [saveVoiceIsPublic, setSaveVoiceIsPublic] = useState<boolean>(false);
  const [isSavingVoice, setIsSavingVoice] = useState<boolean>(false);
  const [saveVoiceStatus, setSaveVoiceStatus] = useState<string | null>(null);

  const pollIntervalRef = useRef<number | null>(null);
  const timeoutRef = useRef<number | null>(null);
  const onJobUpdatedRef = useRef(onJobCreatedOrUpdated);
  useEffect(() => {
    onJobUpdatedRef.current = onJobCreatedOrUpdated;
  }, [onJobCreatedOrUpdated]);

  const fetchRecentPreviews = useCallback(async () => {
    try {
      const items = await api.listVoiceDesignPreviews(12);
      setRecentPreviews(items);
    } catch (err) {
      console.warn("Không thể tải lịch sử bản nghe thử Voice Design:", err);
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const [st, items] = await Promise.all([
          api.getVoiceDesignLlmStatus().catch(() => null),
          api.listVoiceDesignPreviews(12).catch(() => []),
        ]);
        if (!active) return;
        if (st) {
          setLlmStatus(st);
          if (st.active_profile_id) {
            setSelectedLlmProfileId(st.active_profile_id);
          } else if (st.profiles.length > 0) {
            const activeProf = st.profiles.find((p) => p.is_active) || st.profiles[0];
            setSelectedLlmProfileId(activeProf.id);
          }
        }
        setRecentPreviews(items);
      } finally {
        if (active) setLoadingHistory(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const updateAttr = (key: keyof VoiceDesignAttributes, val: string | null) => {
    const next: VoiceDesignAttributes = {
      ...attributes,
      [key]: val || null,
    };
    setAttributes(next);
    if (!manualInstructMode) {
      setInstructText(buildInstructFromAttrs(next));
    }
  };

  const handleManualInstructChange = (val: string) => {
    setInstructText(val);
    setAttributes(parseInstructToAttrs(val));
  };

  const handleCopyInstruct = () => {
    navigator.clipboard.writeText(instructText);
    setCopiedInstruct(true);
    setTimeout(() => setCopiedInstruct(false), 1800);
  };

  const applyProsodyPreset = (preset: string) => {
    setActivePreset(preset);
    setAiTunedBadge(false);
    switch (preset) {
      case "Tự nhiên":
        setSpeed(1.0);
        setNumStep(32);
        setGuidanceScale(2.0);
        setPositionTemperature(5.0);
        setClassTemperature(0.05);
        setTShift(0.10);
        setDenoise(true);
        break;
      case "Kể chuyện":
        setSpeed(0.90);
        setNumStep(38);
        setGuidanceScale(2.2);
        setPositionTemperature(4.2);
        setClassTemperature(0.08);
        setTShift(0.10);
        setDenoise(true);
        break;
      case "Quảng cáo":
        setSpeed(1.10);
        setNumStep(32);
        setGuidanceScale(2.5);
        setPositionTemperature(5.8);
        setClassTemperature(0.15);
        setTShift(0.13);
        setDenoise(true);
        break;
      case "Tin tức":
        setSpeed(1.03);
        setNumStep(36);
        setGuidanceScale(2.8);
        setPositionTemperature(4.5);
        setClassTemperature(0.0);
        setTShift(0.10);
        setDenoise(true);
        break;
      case "Trầm tĩnh":
        setSpeed(0.86);
        setNumStep(40);
        setGuidanceScale(2.4);
        setPositionTemperature(3.8);
        setClassTemperature(0.0);
        setTShift(0.10);
        setDenoise(true);
        break;
    }
  };

  const applyVariant = (v: VoiceDesignVariant, idx: number) => {
    setSelectedVariantIdx(idx);
    setAttributes(v.attributes);
    setManualInstructMode(false);
    setInstructText(v.instruct || buildInstructFromAttrs(v.attributes));
    setSpeed(v.speed);
    setGuidanceScale(v.guidance_scale);
    setPositionTemperature(v.position_temperature);
    setClassTemperature(v.class_temperature);
    setAiTunedBadge(true);
    setActivePreset("");
    if (autoRewriteScript && v.preview_text) {
      setPreviewText(v.preview_text);
    }
  };

  const handleAiAssist = async () => {
    if (!voiceRequest.trim() || assisting) return;
    setAssisting(true);
    setErrorMsg(null);
    try {
      const res = await api.assistVoiceDesign({
        voice_request: voiceRequest.trim(),
        target_language: language || "auto",
        current_preview_text: previewText,
        preserve_preview_text: !autoRewriteScript,
        llm_profile_id: selectedLlmProfileId || null,
      });
      setAssistResult(res);
      setSelectedVariantIdx(0);
      setAttributes(res.attributes);
      setManualInstructMode(false);
      setInstructText(res.instruct);

      const rec = res.recommended_params;
      setSpeed(rec.speed);
      setGuidanceScale(rec.guidance_scale);
      setPositionTemperature(rec.position_temperature);
      setClassTemperature(rec.class_temperature);
      setTShift(rec.t_shift);
      setNumStep(rec.num_step);
      setDenoise(rec.denoise);
      if (rec.language) setLanguage(rec.language);
      setAiTunedBadge(true);
      setActivePreset("");

      if (autoRewriteScript && res.preview_text) {
        setPreviewText(res.preview_text);
      }

      setSuggestedMeta({
        name: res.suggested_name || "",
        slug: res.suggested_slug || "",
        tags: res.suggested_tags || [],
      });
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, "Không thể phân tích yêu cầu bằng AI."));
    } finally {
      setAssisting(false);
    }
  };

  const handleGenerateScript = async (topicOverride?: string) => {
    if (generatingScript) return;
    const chosenTopic = topicOverride || scriptTopic || "Kể chuyện tự nhiên";
    if (topicOverride) setScriptTopic(topicOverride);
    setGeneratingScript(true);
    setErrorMsg(null);
    try {
      const res = await api.generateVoiceDesignScript({
        voice_request: voiceRequest.trim(),
        instruct: instructText.trim(),
        style_or_topic: chosenTopic,
        language: language || "vi",
        include_paralinguistic: false,
        llm_profile_id: selectedLlmProfileId || null,
      });
      if (res.preview_text) {
        setPreviewText(res.preview_text);
      }
      if (res.language) {
        setLanguage(res.language);
      }
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, "Không thể tạo kịch bản mẫu."));
    } finally {
      setGeneratingScript(false);
    }
  };

  const handleInsertCue = (cueTag: string) => {
    const el = scriptTextareaRef.current;
    if (!el) {
      setPreviewText((prev) => `${prev.trim()} ${cueTag} `);
      return;
    }
    const start = el.selectionStart ?? previewText.length;
    const end = el.selectionEnd ?? previewText.length;
    const before = previewText.slice(0, start);
    const after = previewText.slice(end);
    const needSpaceBefore = before.length > 0 && !before.endsWith(" ");
    const needSpaceAfter = after.length > 0 && !after.startsWith(" ");
    const inserted = `${needSpaceBefore ? " " : ""}${cueTag}${needSpaceAfter ? " " : ""}`;
    const nextVal = `${before}${inserted}${after}`;
    setPreviewText(nextVal);
    requestAnimationFrame(() => {
      el.focus();
      const pos = before.length + inserted.length;
      el.setSelectionRange(pos, pos);
    });
  };

  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    setIsPolling(false);
    setLoading(false);
  }, []);

  const handleGenerate = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if ((!voiceRequest.trim() && !instructText.trim()) || !previewText.trim() || loading || isPolling) {
      return;
    }

    setLoading(true);
    setJobId(null);
    setPreviewId(null);
    setJobStatus(null);
    setErrorMsg(null);
    setSuccessMsg(null);
    setLastSavedVoiceId(null);

    const params = {
      denoise,
      guidance_scale: guidanceScale,
      t_shift: tShift,
      position_temperature: positionTemperature,
      class_temperature: classTemperature,
      layer_penalty_factor: layerPenaltyFactor,
      duration: duration ? parseFloat(duration) : undefined,
      preprocess_prompt: preprocessPrompt,
      postprocess_output: postprocessOutput,
      language: language || undefined,
      pad_duration: padDuration !== "" ? parseFloat(padDuration) : undefined,
      fade_duration: fadeDuration !== "" ? parseFloat(fadeDuration) : undefined,
      normalize_text: normalizeText,
    };

    try {
      const res = await api.createVoiceDesignPreview(
        voiceRequest.trim() || instructText.trim(),
        previewText.trim(),
        speed,
        numStep,
        params,
        {
          instruct: instructText.trim() || undefined,
          use_llm: true,
          llm_profile_id: selectedLlmProfileId || null,
        }
      );
      setJobId(res.job_id);
      setPreviewId(res.preview_id);
      setResolvedInstruct(res.instruct || instructText);
      localStorage.setItem("VITE_VOICE_DESIGN_JOB_ID", res.job_id);
      localStorage.setItem("VITE_VOICE_DESIGN_PREVIEW_ID", res.preview_id);
      setJobStatus({
        job_id: res.job_id,
        status: res.status,
        message: res.message,
        progress: 0,
        audio_url: null,
        error_message: null,
      });
      setIsPolling(true);
      onJobCreatedOrUpdated?.();
      void fetchRecentPreviews();
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, "Không thể gửi yêu cầu thiết kế giọng nói."));
      setLoading(false);
    }
  };

  const handleClearJob = () => {
    setJobId(null);
    setPreviewId(null);
    setResolvedInstruct(null);
    setJobStatus(null);
    localStorage.removeItem("VITE_VOICE_DESIGN_JOB_ID");
    localStorage.removeItem("VITE_VOICE_DESIGN_PREVIEW_ID");
  };

  // Restore in-flight job on mount
  useEffect(() => {
    const savedJobId = localStorage.getItem("VITE_VOICE_DESIGN_JOB_ID");
    const savedPreviewId = localStorage.getItem("VITE_VOICE_DESIGN_PREVIEW_ID");
    if (!savedJobId) return;
    let active = true;
    (async () => {
      try {
        if (savedPreviewId && active) setPreviewId(savedPreviewId);
        if (active) setJobId(savedJobId);
        const status = await api.getJobStatus(savedJobId);
        if (!active) return;
        setJobStatus(status);
        setLoading(false);
        if (savedPreviewId) {
          try {
            const pInfo = await api.getPreviewInfo(savedPreviewId);
            if (active && pInfo.instruct) setResolvedInstruct(pInfo.instruct);
          } catch {
            // ignore
          }
        }
        if (status.status !== "completed" && status.status !== "failed") {
          setIsPolling(true);
        }
      } catch {
        if (active) {
          setJobId(null);
          setPreviewId(null);
          setJobStatus(null);
          localStorage.removeItem("VITE_VOICE_DESIGN_JOB_ID");
          localStorage.removeItem("VITE_VOICE_DESIGN_PREVIEW_ID");
          setLoading(false);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // Poll job status with transient error tolerance & visibility resync
  useEffect(() => {
    if (!isPolling || !jobId) return;

    let consecutiveErrors = 0;
    let inFlight = false;

    const runPoll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const status = await api.getJobStatus(jobId);
        consecutiveErrors = 0;
        setJobStatus(status);

        if (status.status === "completed" || status.status === "failed" || status.status === "cancelled") {
          stopPolling();
          onJobUpdatedRef.current?.();
          void fetchRecentPreviews();
        }
      } catch (err: unknown) {
        const msg = getErrorMessage(err);
        const isNotFound = msg.includes("404") || msg.toLowerCase().includes("không tìm thấy job");
        consecutiveErrors += 1;
        if (isNotFound || consecutiveErrors >= 10) {
          setErrorMsg(`Lỗi khi thăm dò trạng thái: ${msg}`);
          stopPolling();
        }
      } finally {
        inFlight = false;
      }
    };

    void runPoll();
    pollIntervalRef.current = window.setInterval(() => {
      void runPoll();
    }, 1500);

    const onVisible = () => {
      if (document.visibilityState === "visible") {
        void runPoll();
        void fetchRecentPreviews();
      }
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);

    timeoutRef.current = window.setTimeout(() => {
      setErrorMsg("Hết thời gian chờ xử lý (Timeout 15 phút).");
      stopPolling();
    }, 900000);

    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };
  }, [isPolling, jobId, stopPolling, fetchRecentPreviews]);

  const handleQuickAccept = async (targetPreviewId?: string) => {
    const pid = targetPreviewId || previewId;
    if (!pid) {
      setErrorMsg("Không tìm thấy mã bản nghe thử (previewId).");
      return;
    }
    setAccepting(true);
    setErrorMsg(null);
    setSuccessMsg(null);

    try {
      const res = await api.acceptPreview(pid, {
        name: suggestedMeta.name || undefined,
        tags: suggestedMeta.tags.length > 0 ? suggestedMeta.tags : undefined,
        is_public: false,
      });
      setLastSavedVoiceId(res.voice_sample_id);
      setSuccessMsg(`Đã kích hoạt mẫu giọng Clone: ${res.voice_sample_id}`);
      onAcceptSuccess(res.voice_sample_id);
      void fetchRecentPreviews();
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, "Lỗi khi chấp nhận giọng thiết kế."));
    } finally {
      setAccepting(false);
    }
  };

  useEffect(() => {
    if (!isSaveModalOpen) return;
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsSaveModalOpen(false);
    };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [isSaveModalOpen]);

  const handleOpenSaveModal = (
    targetPid: string,
    spokenText: string,
    defaultTitleHint?: string
  ) => {
    setSaveTargetPreviewId(targetPid);
    const fallbackName =
      suggestedMeta.name ||
      (defaultTitleHint ? `Thiết kế - ${defaultTitleHint.slice(0, 36)}` : "") ||
      `Giọng Thiết Kế - ${new Date().toLocaleDateString("vi-VN")}`;
    setSaveVoiceName(fallbackName);

    const words = spokenText
      .replace(/\[[^[\]]{1,40}\]/g, " ")
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    setSaveVoiceRefText(words.slice(0, 34).join(" "));
    setSaveVoiceCustomId(suggestedMeta.slug || "");
    setSaveVoiceTags(suggestedMeta.tags.length > 0 ? [...suggestedMeta.tags] : ["Trẻ"]);
    setCustomTagInput("");
    setSaveVoiceIsPublic(false);
    setSaveVoiceStatus(null);
    setIsSaveModalOpen(true);
  };

  const toggleSaveTag = (tag: string) => {
    setSaveVoiceTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  const handleAddCustomTag = () => {
    const clean = customTagInput.trim();
    if (!clean) return;
    if (!saveVoiceTags.includes(clean)) {
      setSaveVoiceTags((prev) => [...prev, clean]);
    }
    setCustomTagInput("");
  };

  const handleSaveVoiceSubmit = async () => {
    const pid = saveTargetPreviewId || previewId;
    if (!pid || !saveVoiceName.trim() || !saveVoiceRefText.trim()) return;
    setIsSavingVoice(true);
    setSaveVoiceStatus(null);

    try {
      const res = await api.saveFavoriteVoice({
        preview_id: pid,
        name: saveVoiceName.trim(),
        is_public: saveVoiceIsPublic,
        ref_text: saveVoiceRefText.trim(),
        custom_id: saveVoiceCustomId.trim() || undefined,
        tags: saveVoiceTags.length > 0 ? saveVoiceTags : undefined,
      });
      setLastSavedVoiceId(res.voice_sample_id);
      setSuccessMsg(`Đã lưu giọng "${saveVoiceName.trim()}" vào Thư viện (${res.voice_sample_id}).`);
      onAcceptSuccess(res.voice_sample_id);
      setSaveVoiceStatus("Lưu giọng thành công vào Thư viện!");
      void fetchRecentPreviews();
      setTimeout(() => {
        setIsSaveModalOpen(false);
      }, 900);
    } catch (err: unknown) {
      setSaveVoiceStatus(`Lỗi: ${getErrorMessage(err, "Không thể lưu giọng.")}`);
    } finally {
      setIsSavingVoice(false);
    }
  };

  const handleRestoreTake = (item: VoiceDesignPreviewHistoryItem) => {
    setVoiceRequest(item.voice_request || "");
    setInstructText(item.instruct || "female, young adult");
    setAttributes(parseInstructToAttrs(item.instruct || ""));
    setManualInstructMode(false);
    setPreviewText(item.preview_text || "");
    if (item.speed !== undefined && item.speed !== null) setSpeed(item.speed);
    if (item.num_step !== undefined && item.num_step !== null) setNumStep(item.num_step);
    if (item.guidance_scale !== undefined && item.guidance_scale !== null)
      setGuidanceScale(item.guidance_scale);
    if (item.position_temperature !== undefined && item.position_temperature !== null)
      setPositionTemperature(item.position_temperature);
    if (item.class_temperature !== undefined && item.class_temperature !== null)
      setClassTemperature(item.class_temperature);
    if (item.t_shift !== undefined && item.t_shift !== null) setTShift(item.t_shift);
    if (item.language) setLanguage(item.language);
  };

  const handleDeleteTake = async (id: string) => {
    if (deletingPreviewId) return;
    setDeletingPreviewId(id);
    try {
      await api.deleteVoiceDesignPreview(id);
      setRecentPreviews((prev) => prev.filter((p) => p.id !== id));
      if (previewId === id) {
        handleClearJob();
      }
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, "Không thể xóa bản nghe thử."));
    } finally {
      setDeletingPreviewId(null);
    }
  };

  const scriptStats = useMemo(
    () => estimatePreviewStats(previewText, language || "vi", speed),
    [previewText, language, speed]
  );

  const durationState = useMemo(() => {
    if (scriptStats.seconds <= 0) {
      return { label: "Chưa có nội dung", tone: "text-muted-foreground" };
    }
    if (scriptStats.seconds < 3.5) {
      return {
        label: `~${scriptStats.seconds}s (Hơi ngắn, nên từ 5–8s để Clone ổn định)`,
        tone: "text-warning",
      };
    }
    if (scriptStats.seconds <= 8.5) {
      return {
        label: `~${scriptStats.seconds}s (Tối ưu cho mẫu Voice Clone 8s)`,
        tone: "text-success",
      };
    }
    return {
      label: `~${scriptStats.seconds}s (Khi lưu yêu thích sẽ cắt 8s đầu)`,
      tone: "text-muted-foreground",
    };
  }, [scriptStats.seconds]);

  const activePlaybackUrl = useMemo(() => {
    if (!jobStatus || jobStatus.status !== "completed") return null;
    if (jobStatus.cdn_audio_url) return jobStatus.cdn_audio_url;
    if (jobStatus.audio_url) return `${api.getApiBaseUrl()}${jobStatus.audio_url}`;
    if (previewId) return `${api.getApiBaseUrl()}/v1/voice-design/previews/${previewId}/audio`;
    return null;
  }, [jobStatus, previewId]);

  return (
    <div className="w-full grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
      {/* LEFT STUDIO COLUMN (7 cols): LLM Architect + OmniVoice Attributes + Preview Script */}
      <div className="lg:col-span-7 flex flex-col gap-6">
        {/* Card 1: AI Voice Architect */}
        <div className="bg-card border border-border rounded-[20px] p-5 flex flex-col gap-4 shadow-sm">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-3.5">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-xl bg-primary/10 text-primary shrink-0">
                <Cpu className="w-4 h-4" />
              </div>
              <div>
                <h2 className="text-sm font-bold text-foreground tracking-tight">
                  1. Ý tưởng giọng nói &amp; Trợ lý AI
                </h2>
                <p className="text-xs text-muted-foreground">
                  Mô tả nhân vật hoặc ngữ cảnh, LLM sẽ chuyển hóa thành thuộc tính và tham số chuẩn OmniVoice.
                </p>
              </div>
            </div>

            {/* Configured LLM Selector / Status */}
            <div className="flex items-center gap-2 shrink-0">
              {llmStatus && llmStatus.profiles.length > 1 ? (
                <select
                  aria-label="Chọn cấu hình LLM hỗ trợ thiết kế giọng"
                  value={selectedLlmProfileId}
                  onChange={(e) => setSelectedLlmProfileId(e.target.value)}
                  className="min-h-[40px] px-3 py-1.5 rounded-xl bg-secondary border border-border text-xs font-semibold text-foreground focus:outline-none focus:ring-2 focus:ring-primary/25"
                >
                  {llmStatus.profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.model})
                    </option>
                  ))}
                </select>
              ) : (
                <div className="px-3 py-1.5 rounded-xl bg-secondary/70 border border-border flex items-center gap-2">
                  <span
                    className={`w-2 h-2 rounded-full ${
                      llmStatus?.is_configured ? "bg-success" : "bg-muted-foreground"
                    }`}
                  />
                  <span className="text-[11px] font-mono font-semibold text-foreground">
                    {llmStatus?.is_configured
                      ? llmStatus.active_model
                      : "Chế độ phân tích cục bộ"}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Quick Persona Inspiration Pills */}
          <div className="flex flex-col gap-1.5">
            <span className="text-[11px] font-semibold text-muted-foreground">
              Mẫu ý tưởng nhanh
            </span>
            <div className="flex flex-wrap gap-1.5">
              {PERSONA_TEMPLATES.map((tpl) => (
                <button
                  key={tpl.label}
                  type="button"
                  onClick={() => {
                    setVoiceRequest(tpl.prompt);
                    setLanguage(tpl.lang);
                  }}
                  className={`min-h-[36px] px-3 py-1.5 rounded-xl border text-xs font-medium transition-colors cursor-pointer ${
                    voiceRequest === tpl.prompt
                      ? "bg-primary/10 border-primary text-primary font-semibold"
                      : "bg-secondary/40 border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
                  }`}
                >
                  {tpl.label}
                </button>
              ))}
            </div>
          </div>

          {/* Natural Description Textarea */}
          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="vd-voice-brief"
              className="text-xs font-semibold text-foreground"
            >
              Mô tả chi tiết giọng mong muốn
            </label>
            <textarea
              id="vd-voice-brief"
              value={voiceRequest}
              onChange={(e) => setVoiceRequest(e.target.value)}
              rows={3}
              placeholder="Ví dụ: Giọng nam trung niên trầm ấm, đĩnh đạc để thuyết minh phim tài liệu khoa học..."
              className="w-full bg-background border border-input rounded-xl p-3.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/25 focus:border-primary resize-y"
            />
          </div>

          {/* AI Assist Action Row */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-1">
            <label className="flex items-center gap-2 text-xs font-medium text-muted-foreground cursor-pointer select-none min-h-[44px]">
              <input
                type="checkbox"
                checked={autoRewriteScript}
                onChange={(e) => setAutoRewriteScript(e.target.checked)}
                className="w-4 h-4 rounded border-border accent-primary cursor-pointer"
              />
              <span>Tự động soạn kịch bản nghe thử khớp nhân vật</span>
            </label>

            <button
              type="button"
              onClick={handleAiAssist}
              disabled={assisting || !voiceRequest.trim()}
              className={`min-h-[44px] px-4 py-2.5 rounded-xl text-xs font-bold transition-colors active:translate-y-[1px] flex items-center justify-center gap-2 cursor-pointer shrink-0 ${
                !assisting && voiceRequest.trim()
                  ? "bg-primary text-primary-foreground hover:bg-primary-hover"
                  : "bg-muted text-muted-foreground cursor-not-allowed"
              }`}
            >
              {assisting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Đang phân tích với AI...</span>
                </>
              ) : (
                <>
                  <Wand2 className="w-4 h-4" />
                  <span>Phân tích &amp; Thiết kế bằng AI</span>
                </>
              )}
            </button>
          </div>

          {/* AI Assist Result & 3 Persona Variants */}
          {assistResult && (
            <div className="mt-1 p-4 rounded-2xl bg-secondary/40 border border-border flex flex-col gap-3.5 animate-fadeIn">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-primary shrink-0" />
                  <span className="text-xs font-bold text-foreground">
                    Đề xuất từ Đạo diễn Âm thanh AI
                  </span>
                </div>
                <span className="text-[11px] font-mono px-2.5 py-0.5 rounded-full bg-card border border-border text-muted-foreground">
                  {assistResult.source === "llm" && assistResult.llm_model
                    ? `LLM: ${assistResult.llm_model}`
                    : "Phân tích ngữ nghĩa cục bộ"}
                </span>
              </div>

              <p className="text-xs text-muted-foreground leading-relaxed">
                {assistResult.explanation}
              </p>

              {assistResult.variants && assistResult.variants.length > 0 && (
                <div className="flex flex-col gap-2">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                    Chọn phương án phối giọng ({assistResult.variants.length})
                  </span>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-2.5">
                    {assistResult.variants.map((v, idx) => {
                      const isSelected = selectedVariantIdx === idx;
                      return (
                        <button
                          key={`${v.label}-${idx}`}
                          type="button"
                          onClick={() => applyVariant(v, idx)}
                          className={`text-left p-3 rounded-xl border transition-all cursor-pointer flex flex-col justify-between gap-2 min-h-[96px] ${
                            isSelected
                              ? "bg-card border-primary ring-1 ring-primary/30"
                              : "bg-card/60 border-border hover:border-primary/40"
                          }`}
                        >
                          <div className="flex flex-col gap-1">
                            <div className="flex items-center justify-between gap-1.5">
                              <span className="text-xs font-bold text-foreground line-clamp-1">
                                {v.label}
                              </span>
                              {isSelected && (
                                <Check className="w-3.5 h-3.5 text-primary shrink-0" />
                              )}
                            </div>
                            <p className="text-[11px] text-muted-foreground line-clamp-2 leading-snug">
                              {v.summary}
                            </p>
                          </div>
                          <div className="flex items-center justify-between gap-1 pt-1.5 border-t border-border/60 text-[10px] font-mono text-muted-foreground">
                            <span className="truncate">{v.instruct}</span>
                            <span className="shrink-0">{v.speed.toFixed(2)}x</span>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Card 2: Interactive OmniVoice Attribute Matrix */}
        <div className="bg-card border border-border rounded-[20px] p-5 flex flex-col gap-4 shadow-sm">
          <div className="flex items-center justify-between gap-2 border-b border-border pb-3">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-xl bg-secondary text-foreground shrink-0">
                <Sliders className="w-4 h-4" />
              </div>
              <div>
                <h2 className="text-sm font-bold text-foreground tracking-tight">
                  2. Cấu trúc thuộc tính chuẩn OmniVoice
                </h2>
                <p className="text-xs text-muted-foreground">
                  Tinh chỉnh trực quan từng nhóm thuộc tính âm học (tối đa 1 thẻ mỗi nhóm).
                </p>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Gender */}
            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold text-foreground">Giới tính (Gender)</span>
              <div className="grid grid-cols-2 gap-2">
                {GENDER_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => updateAttr("gender", opt.value)}
                    className={`min-h-[44px] px-3 py-2 rounded-xl border text-xs font-semibold transition-colors cursor-pointer ${
                      attributes.gender === opt.value
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-background border-border text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Style */}
            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold text-foreground">Phong cách phát âm (Style)</span>
              <div className="grid grid-cols-2 gap-2">
                {STYLE_OPTIONS.map((opt) => {
                  const active = (attributes.style || "") === opt.value;
                  return (
                    <button
                      key={opt.label}
                      type="button"
                      onClick={() => updateAttr("style", opt.value || null)}
                      className={`min-h-[44px] px-3 py-2 rounded-xl border text-xs font-semibold transition-colors cursor-pointer ${
                        active
                          ? "bg-primary text-primary-foreground border-primary"
                          : "bg-background border-border text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                      }`}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Age */}
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold text-foreground">Độ tuổi (Age)</span>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              {AGE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() =>
                    updateAttr("age", attributes.age === opt.value ? null : opt.value)
                  }
                  className={`min-h-[44px] px-2.5 py-2 rounded-xl border text-xs font-semibold transition-colors cursor-pointer ${
                    attributes.age === opt.value
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-background border-border text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Pitch */}
          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-semibold text-foreground">Cao độ (Pitch)</span>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
              {PITCH_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() =>
                    updateAttr("pitch", attributes.pitch === opt.value ? null : opt.value)
                  }
                  className={`min-h-[44px] px-2.5 py-2 rounded-xl border text-xs font-semibold transition-colors cursor-pointer ${
                    attributes.pitch === opt.value
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-background border-border text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Foreign Accent */}
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between gap-2">
              <label htmlFor="vd-accent-select" className="text-xs font-semibold text-foreground">
                Ngữ điệu / Accent ngoại ngữ (Tùy chọn)
              </label>
              {language === "vi" && attributes.accent && (
                <span className="text-[11px] text-warning font-medium">
                  Accent chỉ có hiệu lực rõ khi văn bản là tiếng Anh/Trung
                </span>
              )}
            </div>
            <select
              id="vd-accent-select"
              value={attributes.accent || ""}
              onChange={(e) => updateAttr("accent", e.target.value || null)}
              className="min-h-[44px] px-3.5 py-2 rounded-xl bg-background border border-input text-xs font-semibold text-foreground focus:outline-none focus:ring-2 focus:ring-primary/25"
            >
              <option value="">Mặc định theo ngôn ngữ văn bản</option>
              {ACCENT_GROUPS.map((grp) => (
                <optgroup key={grp.label} label={grp.label}>
                  {grp.options.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          {/* Compiled OmniVoice Instruct Bar */}
          <div className="p-3.5 rounded-xl bg-secondary/50 border border-border flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                Chuỗi lệnh OmniVoice Instruct
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setManualInstructMode((prev) => !prev)}
                  className="min-h-[32px] px-2.5 py-1 rounded-lg bg-card border border-border text-[11px] font-semibold text-muted-foreground hover:text-foreground transition-colors cursor-pointer flex items-center gap-1"
                >
                  <Pencil className="w-3 h-3" />
                  <span>{manualInstructMode ? "Khóa theo bảng chọn" : "Sửa trực tiếp"}</span>
                </button>
                <button
                  type="button"
                  onClick={handleCopyInstruct}
                  className="min-h-[32px] px-2.5 py-1 rounded-lg bg-card border border-border text-[11px] font-semibold text-muted-foreground hover:text-foreground transition-colors cursor-pointer flex items-center gap-1"
                >
                  {copiedInstruct ? (
                    <>
                      <Check className="w-3 h-3 text-success" />
                      <span>Đã chép</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" />
                      <span>Sao chép</span>
                    </>
                  )}
                </button>
              </div>
            </div>

            {manualInstructMode ? (
              <input
                type="text"
                aria-label="Chuỗi lệnh OmniVoice Instruct"
                value={instructText}
                onChange={(e) => handleManualInstructChange(e.target.value)}
                placeholder="female, young adult, low pitch"
                className="min-h-[44px] w-full px-3 py-2 rounded-xl bg-background border border-primary/50 font-mono text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-primary/25"
              />
            ) : (
              <div className="font-mono text-xs font-semibold text-foreground bg-card border border-border/80 rounded-lg px-3 py-2.5 select-all">
                {instructText}
              </div>
            )}
          </div>
        </div>

        {/* Card 3: Preview Script & Paralinguistic Studio */}
        <div className="bg-card border border-border rounded-[20px] p-5 flex flex-col gap-4 shadow-sm">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-3">
            <div>
              <h2 className="text-sm font-bold text-foreground tracking-tight">
                3. Kịch bản nghe thử &amp; Tạo mẫu Clone
              </h2>
              <p className="text-xs text-muted-foreground">
                Câu thoại dài 6–8 giây giàu ngữ điệu sẽ cho chất lượng nhân bản giọng (Voice Clone) tốt nhất.
              </p>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              <select
                aria-label="Ngôn ngữ kịch bản nghe thử"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="min-h-[40px] px-3 py-1.5 rounded-xl bg-secondary border border-border text-xs font-semibold text-foreground focus:outline-none focus:ring-2 focus:ring-primary/25"
              >
                <option value="vi">Tiếng Việt (vi)</option>
                <option value="en">English (en)</option>
                <option value="zh">中文 (zh)</option>
                <option value="ja">日本語 (ja)</option>
                <option value="ko">한국어 (ko)</option>
              </select>

              <button
                type="button"
                onClick={() => handleGenerateScript()}
                disabled={generatingScript}
                className="min-h-[40px] px-3.5 py-1.5 rounded-xl bg-secondary hover:bg-secondary/80 border border-border text-xs font-bold text-foreground transition-colors cursor-pointer flex items-center gap-1.5"
              >
                {generatingScript ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Sparkles className="w-3.5 h-3.5 text-primary" />
                )}
                <span>AI Viết câu mẫu</span>
              </button>
            </div>
          </div>

          {/* Topic quick buttons */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] font-semibold text-muted-foreground mr-1">
              Chủ đề mẫu:
            </span>
            {SCRIPT_TOPICS.map((tp) => (
              <button
                key={tp}
                type="button"
                disabled={generatingScript}
                onClick={() => handleGenerateScript(tp)}
                className={`min-h-[34px] px-2.5 py-1 rounded-lg border text-xs transition-colors cursor-pointer ${
                  scriptTopic === tp
                    ? "bg-primary/10 border-primary/50 text-primary font-semibold"
                    : "bg-background border-border text-muted-foreground hover:text-foreground"
                }`}
              >
                {tp}
              </button>
            ))}
          </div>

          {/* Script Textarea */}
          <div className="flex flex-col gap-2">
            <label htmlFor="vd-preview-script" className="sr-only">
              Nội dung kịch bản nghe thử
            </label>
            <textarea
              id="vd-preview-script"
              ref={scriptTextareaRef}
              value={previewText}
              onChange={(e) => setPreviewText(e.target.value)}
              rows={3}
              maxLength={600}
              placeholder="Nhập câu thoại mẫu để nghe thử và tạo giọng tham chiếu..."
              className="w-full bg-background border border-input rounded-xl p-3.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/25 focus:border-primary resize-y leading-relaxed"
            />

            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className={`font-medium ${durationState.tone}`}>
                {durationState.label}
              </span>
              <span className="font-mono text-muted-foreground">
                {scriptStats.words} từ • {previewText.length}/600 ký tự
              </span>
            </div>
          </div>

          {/* Paralinguistic Tag Inserter */}
          <div className="flex flex-col gap-1.5 pt-1 border-t border-border/60">
            <span className="text-[11px] font-semibold text-muted-foreground">
              Chèn biểu cảm phi ngôn từ vào vị trí con trỏ (tối đa 1 thẻ mỗi câu):
            </span>
            <div className="flex flex-wrap gap-1.5">
              {PARALINGUISTIC_CUES.map((cue) => (
                <button
                  key={cue.tag}
                  type="button"
                  onClick={() => handleInsertCue(cue.tag)}
                  className="min-h-[34px] px-2.5 py-1 rounded-lg bg-secondary/50 hover:bg-secondary border border-border text-[11px] font-mono text-foreground transition-colors cursor-pointer"
                >
                  + {cue.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* RIGHT STUDIO COLUMN (5 cols): Generate CTA + Acoustic Tuning + Active Preview + Recent Takes */}
      <div className="lg:col-span-5 flex flex-col gap-6">
        {/* Primary Generate Action */}
        <div className="flex flex-col gap-3">
          <button
            type="button"
            onClick={() => void handleGenerate()}
            disabled={loading || isPolling || !previewText.trim() || (!voiceRequest.trim() && !instructText.trim())}
            className={`w-full min-h-[52px] px-6 py-3 rounded-xl font-bold text-base transition-colors active:translate-y-[1px] flex items-center justify-center gap-2.5 cursor-pointer ${
              !loading && !isPolling && previewText.trim() && (voiceRequest.trim() || instructText.trim())
                ? "bg-primary hover:bg-primary-hover text-primary-foreground"
                : "bg-secondary text-muted-foreground cursor-not-allowed"
            }`}
          >
            {loading || isPolling ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                <span>Đang tổng hợp bản nghe thử...</span>
              </>
            ) : (
              <>
                <Play className="w-5 h-5 fill-current" />
                <span>Tạo bản nghe thử giọng</span>
              </>
            )}
          </button>

          {errorMsg && (
            <div className="p-3.5 bg-destructive/10 border border-destructive/20 text-destructive rounded-xl text-xs font-semibold">
              {errorMsg}
            </div>
          )}

          {successMsg && (
            <div className="p-3.5 bg-success/10 border border-success/20 text-success rounded-xl text-xs font-semibold flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <Check className="w-4 h-4 shrink-0" />
                <span>{successMsg}</span>
              </div>
              {lastSavedVoiceId && onUseVoiceInTTS && (
                <button
                  type="button"
                  onClick={() => onUseVoiceInTTS(lastSavedVoiceId)}
                  className="min-h-[38px] self-start px-3.5 py-1.5 rounded-lg bg-card border border-border text-foreground hover:border-primary text-xs font-bold flex items-center gap-1.5 cursor-pointer transition-colors"
                >
                  <span>Chuyển sang Text to Speech với giọng này</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          )}
        </div>

        {/* Active Preview Output Card */}
        {jobId && jobStatus && (
          <SectionCard title="Kết quả bản nghe thử hiện tại">
            <div className="flex flex-col gap-3.5">
              <div className="flex items-center justify-between gap-2">
                {resolvedInstruct && (
                  <span className="text-[11px] font-mono px-2.5 py-1 rounded-lg bg-secondary border border-border text-foreground truncate">
                    {resolvedInstruct}
                  </span>
                )}
                {(jobStatus.status === "completed" || jobStatus.status === "failed") && (
                  <button
                    type="button"
                    onClick={handleClearJob}
                    className="text-xs text-muted-foreground hover:text-foreground underline cursor-pointer font-semibold shrink-0 ml-auto"
                  >
                    Ẩn kết quả
                  </button>
                )}
              </div>

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

              {(jobStatus.text || previewText) && (
                <div className="bg-secondary/30 border border-border rounded-xl p-3 flex flex-col gap-1">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                    Văn bản TTS đối soát:
                  </span>
                  <p className="text-xs text-foreground whitespace-pre-wrap leading-relaxed select-text">
                    {jobStatus.text || previewText}
                  </p>
                </div>
              )}

              {jobStatus.status === "completed" && activePlaybackUrl && (
                <div className="flex flex-col gap-3 p-3.5 rounded-2xl bg-secondary/35 border border-border">
                  <button
                    type="button"
                    onClick={() => {
                      if (currentPlayUrl === activePlaybackUrl) {
                        onTogglePlay();
                      } else {
                        onPlayAudio(
                          activePlaybackUrl,
                          suggestedMeta.name || `Bản thiết kế (${resolvedInstruct || instructText})`
                        );
                      }
                    }}
                    className={`w-full min-h-[44px] py-2.5 px-5 rounded-full font-bold text-xs transition-colors flex items-center justify-center gap-2 cursor-pointer border ${
                      currentPlayUrl === activePlaybackUrl && globalPlayerPlaying
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-card hover:bg-secondary text-foreground border-border"
                    }`}
                  >
                    {currentPlayUrl === activePlaybackUrl && globalPlayerPlaying ? (
                      <>
                        <Pause className="w-4 h-4 fill-current" />
                        <span>Tạm dừng nghe thử</span>
                      </>
                    ) : (
                      <>
                        <Play className="w-4 h-4 fill-current" />
                        <span>Nghe bản thiết kế</span>
                      </>
                    )}
                  </button>

                  {previewId && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                      <button
                        type="button"
                        onClick={() => handleOpenSaveModal(previewId, previewText, voiceRequest)}
                        className="min-h-[44px] px-4 py-2 rounded-full bg-primary hover:bg-primary-hover text-primary-foreground font-bold text-xs transition-colors flex items-center justify-center gap-1.5 cursor-pointer"
                      >
                        <Heart className="w-3.5 h-3.5" />
                        <span>Lưu vào Thư viện</span>
                      </button>

                      <button
                        type="button"
                        onClick={() => void handleQuickAccept(previewId)}
                        disabled={accepting}
                        className="min-h-[44px] px-4 py-2 rounded-full bg-card hover:bg-secondary border border-border text-foreground font-bold text-xs transition-colors flex items-center justify-center gap-1.5 cursor-pointer"
                      >
                        {accepting ? (
                          <>
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            <span>Đang kích hoạt...</span>
                          </>
                        ) : (
                          <>
                            <Check className="w-3.5 h-3.5" />
                            <span>Dùng nhanh làm Clone</span>
                          </>
                        )}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </SectionCard>
        )}

        {/* Acoustic & Prosody Tuning Card */}
        <SectionCard title="Tinh chỉnh âm học &amp; Nhịp điệu">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-foreground">
                  Preset sắc thái nói
                </span>
                {aiTunedBadge && (
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/30 font-semibold">
                    Đã tối ưu bởi AI
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {["Tự nhiên", "Kể chuyện", "Quảng cáo", "Tin tức", "Trầm tĩnh"].map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => applyProsodyPreset(p)}
                    className={`min-h-[36px] px-3 py-1.5 rounded-xl border text-xs font-semibold transition-colors cursor-pointer ${
                      activePreset === p
                        ? "bg-primary/10 border-primary text-primary"
                        : "bg-secondary/40 border-border text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-1">
              <div className="flex flex-col gap-1.5">
                <div className="flex justify-between items-center text-xs">
                  <label htmlFor="vd-speed" className="font-semibold text-foreground">
                    Tốc độ (Speed)
                  </label>
                  <span className="font-mono text-muted-foreground">{speed.toFixed(2)}x</span>
                </div>
                <input
                  id="vd-speed"
                  type="range"
                  min="0.5"
                  max="2.0"
                  step="0.02"
                  value={speed}
                  onChange={(e) => setSpeed(parseFloat(e.target.value))}
                  className="seekbar w-full"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <div className="flex justify-between items-center text-xs">
                  <label htmlFor="vd-guidance" className="font-semibold text-foreground">
                    Guidance Scale
                  </label>
                  <span className="font-mono text-muted-foreground">
                    {guidanceScale.toFixed(1)}
                  </span>
                </div>
                <input
                  id="vd-guidance"
                  type="range"
                  min="0.5"
                  max="5.0"
                  step="0.1"
                  value={guidanceScale}
                  onChange={(e) => setGuidanceScale(parseFloat(e.target.value))}
                  className="seekbar w-full"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <div className="flex justify-between items-center text-xs">
                  <label htmlFor="vd-pos-temp" className="font-semibold text-foreground">
                    Biểu cảm nhịp (Pos Temp)
                  </label>
                  <span className="font-mono text-muted-foreground">
                    {positionTemperature.toFixed(1)}
                  </span>
                </div>
                <input
                  id="vd-pos-temp"
                  type="range"
                  min="0.0"
                  max="10.0"
                  step="0.2"
                  value={positionTemperature}
                  onChange={(e) => setPositionTemperature(parseFloat(e.target.value))}
                  className="seekbar w-full"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <div className="flex justify-between items-center text-xs">
                  <label htmlFor="vd-steps" className="font-semibold text-foreground">
                    Độ chi tiết (Steps)
                  </label>
                  <span className="font-mono text-muted-foreground">{numStep}</span>
                </div>
                <input
                  id="vd-steps"
                  type="range"
                  min="10"
                  max="64"
                  step="1"
                  value={numStep}
                  onChange={(e) => setNumStep(parseInt(e.target.value, 10))}
                  className="seekbar w-full"
                />
              </div>
            </div>

            {/* Expandable Advanced Parameters */}
            <div className="border-t border-border pt-2">
              <button
                type="button"
                onClick={() => setShowAdvanced((prev) => !prev)}
                className="min-h-[40px] w-full flex items-center justify-between text-xs font-bold text-muted-foreground hover:text-foreground cursor-pointer"
              >
                <span className="flex items-center gap-2">
                  <Settings2 className="w-4 h-4" />
                  <span>Tham số kỹ thuật chuyên sâu</span>
                </span>
                {showAdvanced ? (
                  <ChevronUp className="w-4 h-4" />
                ) : (
                  <ChevronDown className="w-4 h-4" />
                )}
              </button>

              {showAdvanced && (
                <div className="flex flex-col gap-4 mt-3 p-3.5 rounded-xl bg-secondary/30 border border-border">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                    <label className="flex items-center gap-2 text-xs font-medium text-foreground cursor-pointer">
                      <input
                        type="checkbox"
                        checked={denoise}
                        onChange={(e) => setDenoise(e.target.checked)}
                        className="w-4 h-4 rounded accent-primary"
                      />
                      <span>Lọc nhiễu (Denoise)</span>
                    </label>
                    <label className="flex items-center gap-2 text-xs font-medium text-foreground cursor-pointer">
                      <input
                        type="checkbox"
                        checked={normalizeText}
                        onChange={(e) => setNormalizeText(e.target.checked)}
                        className="w-4 h-4 rounded accent-primary"
                      />
                      <span>Chuẩn hóa số/ngày (Normalize)</span>
                    </label>
                    <label className="flex items-center gap-2 text-xs font-medium text-foreground cursor-pointer">
                      <input
                        type="checkbox"
                        checked={preprocessPrompt}
                        onChange={(e) => setPreprocessPrompt(e.target.checked)}
                        className="w-4 h-4 rounded accent-primary"
                      />
                      <span>Tiền xử lý tham chiếu</span>
                    </label>
                    <label className="flex items-center gap-2 text-xs font-medium text-foreground cursor-pointer">
                      <input
                        type="checkbox"
                        checked={postprocessOutput}
                        onChange={(e) => setPostprocessOutput(e.target.checked)}
                        className="w-4 h-4 rounded accent-primary"
                      />
                      <span>Hậu xử lý đầu ra</span>
                    </label>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                    <div className="flex flex-col gap-1">
                      <div className="flex justify-between text-xs">
                        <span className="text-muted-foreground font-medium">Class Temp</span>
                        <span className="font-mono text-foreground">
                          {classTemperature.toFixed(2)}
                        </span>
                      </div>
                      <input
                        type="range"
                        min="0.0"
                        max="5.0"
                        step="0.05"
                        value={classTemperature}
                        onChange={(e) => setClassTemperature(parseFloat(e.target.value))}
                        className="seekbar w-full"
                      />
                    </div>

                    <div className="flex flex-col gap-1">
                      <div className="flex justify-between text-xs">
                        <span className="text-muted-foreground font-medium">t_shift</span>
                        <span className="font-mono text-foreground">{tShift.toFixed(2)}</span>
                      </div>
                      <input
                        type="range"
                        min="0.01"
                        max="0.50"
                        step="0.01"
                        value={tShift}
                        onChange={(e) => setTShift(parseFloat(e.target.value))}
                        className="seekbar w-full"
                      />
                    </div>

                    <div className="flex flex-col gap-1">
                      <div className="flex justify-between text-xs">
                        <span className="text-muted-foreground font-medium">Layer Penalty</span>
                        <span className="font-mono text-foreground">
                          {layerPenaltyFactor.toFixed(1)}
                        </span>
                      </div>
                      <input
                        type="range"
                        min="0.0"
                        max="10.0"
                        step="0.5"
                        value={layerPenaltyFactor}
                        onChange={(e) => setLayerPenaltyFactor(parseFloat(e.target.value))}
                        className="seekbar w-full"
                      />
                    </div>

                    <div className="flex flex-col gap-1">
                      <span className="text-xs text-muted-foreground font-medium">
                        Duration cố định (giây)
                      </span>
                      <input
                        type="number"
                        min="0.5"
                        step="0.1"
                        value={duration}
                        onChange={(e) => setDuration(e.target.value)}
                        placeholder="Tự động"
                        className="min-h-[38px] px-3 py-1.5 rounded-lg bg-card border border-border text-xs font-mono text-foreground"
                      />
                    </div>

                    <div className="flex flex-col gap-1">
                      <span className="text-xs text-muted-foreground font-medium">
                        Pad Duration (s)
                      </span>
                      <input
                        type="number"
                        min="0"
                        max="2.0"
                        step="0.05"
                        value={padDuration}
                        onChange={(e) => setPadDuration(e.target.value)}
                        placeholder="Mặc định"
                        className="min-h-[38px] px-3 py-1.5 rounded-lg bg-card border border-border text-xs font-mono text-foreground"
                      />
                    </div>

                    <div className="flex flex-col gap-1">
                      <span className="text-xs text-muted-foreground font-medium">
                        Fade Duration (s)
                      </span>
                      <input
                        type="number"
                        min="0"
                        max="1.0"
                        step="0.05"
                        value={fadeDuration}
                        onChange={(e) => setFadeDuration(e.target.value)}
                        placeholder="Mặc định"
                        className="min-h-[38px] px-3 py-1.5 rounded-lg bg-card border border-border text-xs font-mono text-foreground"
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </SectionCard>

        {/* Recent Audition Takes (A/B Comparison) */}
        <SectionCard title="Các bản thử nghiệm gần đây (So sánh A/B)">
          {loadingHistory && recentPreviews.length === 0 ? (
            <div className="py-6 flex items-center justify-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Đang tải danh sách bản thử...</span>
            </div>
          ) : recentPreviews.length === 0 ? (
            <p className="text-xs text-muted-foreground py-3">
              Chưa có bản thiết kế nào. Hãy mô tả giọng ở khung bên trái và nhấn &ldquo;Tạo bản nghe thử giọng&rdquo;.
            </p>
          ) : (
            <div className="flex flex-col gap-2.5 max-h-[420px] overflow-y-auto pr-1">
              {recentPreviews.map((item) => {
                const takeUrl =
                  item.cdn_audio_url ||
                  (item.audio_url ? `${api.getApiBaseUrl()}${item.audio_url}` : null);
                const isPlayingThis = Boolean(takeUrl && currentPlayUrl === takeUrl && globalPlayerPlaying);

                return (
                  <div
                    key={item.id}
                    className={`p-3 rounded-xl border transition-colors flex flex-col gap-2 ${
                      takeUrl && currentPlayUrl === takeUrl
                        ? "bg-primary/5 border-primary/50"
                        : "bg-background/60 border-border"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-semibold text-foreground line-clamp-1">
                          {item.voice_request || item.instruct}
                        </p>
                        <div className="flex flex-wrap items-center gap-1.5 mt-1">
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-secondary border border-border text-muted-foreground truncate max-w-[220px]">
                            {item.instruct}
                          </span>
                          {item.speed && (
                            <span className="text-[10px] font-mono text-muted-foreground">
                              {item.speed.toFixed(2)}x
                            </span>
                          )}
                          {item.accepted_sample_id && (
                            <span className="text-[10px] font-semibold text-success bg-success/10 px-2 py-0.5 rounded">
                              Đã lưu mẫu
                            </span>
                          )}
                        </div>
                      </div>

                      <button
                        type="button"
                        aria-label="Xóa bản thử nghiệm"
                        disabled={deletingPreviewId === item.id}
                        onClick={() => void handleDeleteTake(item.id)}
                        className="min-w-[34px] min-h-[34px] flex items-center justify-center rounded-lg text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors cursor-pointer shrink-0"
                        title="Xóa bản nghe thử này"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>

                    <p className="text-[11px] text-muted-foreground line-clamp-1">
                      &ldquo;{item.preview_text}&rdquo;
                    </p>

                    <div className="flex flex-wrap items-center gap-2 pt-1">
                      {item.status === "completed" && takeUrl ? (
                        <>
                          <button
                            type="button"
                            onClick={() => {
                              if (currentPlayUrl === takeUrl) {
                                onTogglePlay();
                              } else {
                                onPlayAudio(takeUrl, `Take: ${item.instruct}`);
                              }
                            }}
                            className={`min-h-[36px] px-3.5 py-1 rounded-full text-xs font-bold flex items-center gap-1.5 cursor-pointer border transition-colors ${
                              isPlayingThis
                                ? "bg-primary text-primary-foreground border-primary"
                                : "bg-card hover:bg-secondary text-foreground border-border"
                            }`}
                          >
                            {isPlayingThis ? (
                              <>
                                <Pause className="w-3 h-3 fill-current" />
                                <span>Dừng</span>
                              </>
                            ) : (
                              <>
                                <Play className="w-3 h-3 fill-current" />
                                <span>Nghe</span>
                              </>
                            )}
                          </button>

                          <button
                            type="button"
                            onClick={() =>
                              handleOpenSaveModal(item.id, item.preview_text, item.voice_request)
                            }
                            className="min-h-[36px] px-3 py-1 rounded-full bg-card hover:bg-secondary border border-border text-xs font-semibold text-foreground flex items-center gap-1 cursor-pointer transition-colors"
                          >
                            <Heart className="w-3 h-3" />
                            <span>Lưu mẫu</span>
                          </button>
                        </>
                      ) : (
                        <span className="text-[11px] font-mono text-muted-foreground">
                          Trạng thái: {item.status}
                        </span>
                      )}

                      <button
                        type="button"
                        onClick={() => handleRestoreTake(item)}
                        className="min-h-[36px] px-3 py-1 rounded-full bg-secondary/60 hover:bg-secondary border border-border text-xs font-semibold text-muted-foreground hover:text-foreground flex items-center gap-1 cursor-pointer transition-colors ml-auto"
                        title="Nạp lại mô tả, thuộc tính và tham số của bản thử này"
                      >
                        <RotateCcw className="w-3 h-3" />
                        <span>Nạp lại</span>
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </SectionCard>
      </div>

      {/* Save Designed Voice to Library Modal */}
      {isSaveModalOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="save-voice-design-modal-title"
          className="fixed inset-0 z-[70] flex items-center justify-center p-4 lg:pl-fluid-sidebar bg-black/55 backdrop-blur-sm animate-fadeIn"
          onClick={() => setIsSaveModalOpen(false)}
        >
          <div
            className="bg-card border border-border rounded-[20px] p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto flex flex-col gap-4 shadow-2xl relative"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              aria-label="Đóng cửa sổ lưu giọng"
              onClick={() => setIsSaveModalOpen(false)}
              className="min-w-[44px] min-h-[44px] flex items-center justify-center absolute top-3 right-3 rounded-xl text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors cursor-pointer"
            >
              <X className="w-5 h-5" />
            </button>

            <div className="flex items-center gap-3 border-b border-border pb-3 pr-10">
              <div className="p-2.5 bg-primary/10 rounded-xl text-primary">
                <Heart className="w-5 h-5" />
              </div>
              <div>
                <h3
                  id="save-voice-design-modal-title"
                  className="font-bold text-base text-foreground"
                >
                  Lưu giọng thiết kế vào Thư viện
                </h3>
                <p className="text-xs text-muted-foreground">
                  Cắt tối đa 8 giây đầu làm mẫu chuẩn để nhân bản giọng (Voice Clone)
                </p>
              </div>
            </div>

            {saveVoiceStatus && (
              <div
                className={`p-3.5 rounded-xl text-xs font-semibold border ${
                  saveVoiceStatus.startsWith("Lỗi")
                    ? "bg-destructive/10 border-destructive/20 text-destructive"
                    : "bg-success/10 border-success/20 text-success"
                }`}
              >
                {saveVoiceStatus}
              </div>
            )}

            <div className="flex flex-col gap-3.5">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-semibold text-foreground">
                    Tên giọng mẫu
                  </label>
                  <input
                    type="text"
                    value={saveVoiceName}
                    onChange={(e) => setSaveVoiceName(e.target.value)}
                    placeholder="Ví dụ: Nữ Trẻ Kể Chuyện Đêm Khuya"
                    className="min-h-[44px] bg-background border border-input rounded-xl px-3 py-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-primary/25 font-semibold"
                    required
                  />
                </div>

                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-semibold text-foreground">
                    Mã ID (slug - tùy chọn)
                  </label>
                  <input
                    type="text"
                    value={saveVoiceCustomId}
                    onChange={(e) => setSaveVoiceCustomId(e.target.value)}
                    placeholder="nu_tre_ke_chuyen"
                    className="min-h-[44px] bg-background border border-input rounded-xl px-3 py-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-primary/25 font-mono"
                  />
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-foreground">
                  Văn bản tham chiếu (ref_text trong 8 giây đầu)
                </label>
                <textarea
                  value={saveVoiceRefText}
                  onChange={(e) => setSaveVoiceRefText(e.target.value)}
                  rows={2}
                  className="bg-background border border-input rounded-xl p-3 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-primary/25 resize-y"
                  required
                />
              </div>

              {/* Tags Picker */}
              <div className="flex flex-col gap-2">
                <label className="text-xs font-semibold text-foreground">
                  Nhãn phân loại (Tags)
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {LIBRARY_PRESET_TAGS.map((tag) => {
                    const active = saveVoiceTags.includes(tag);
                    return (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => toggleSaveTag(tag)}
                        className={`min-h-[34px] px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors cursor-pointer ${
                          active
                            ? "bg-primary text-primary-foreground border-primary font-semibold"
                            : "bg-background border-border text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {tag}
                      </button>
                    );
                  })}
                  {saveVoiceTags
                    .filter((t) => !LIBRARY_PRESET_TAGS.includes(t))
                    .map((customTag) => (
                      <button
                        key={customTag}
                        type="button"
                        onClick={() => toggleSaveTag(customTag)}
                        className="min-h-[34px] px-2.5 py-1 rounded-lg bg-primary text-primary-foreground border border-primary text-xs font-semibold cursor-pointer"
                      >
                        {customTag} ×
                      </button>
                    ))}
                </div>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={customTagInput}
                    onChange={(e) => setCustomTagInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleAddCustomTag();
                      }
                    }}
                    placeholder="Thêm nhãn tùy chỉnh..."
                    className="min-h-[40px] flex-1 bg-background border border-input rounded-xl px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-primary/25"
                  />
                  <button
                    type="button"
                    onClick={handleAddCustomTag}
                    className="min-h-[40px] px-3 rounded-xl bg-secondary hover:bg-secondary/80 border border-border text-xs font-semibold text-foreground cursor-pointer flex items-center gap-1"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    <span>Thêm</span>
                  </button>
                </div>
              </div>

              {/* Visibility */}
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-foreground">
                  Quyền truy cập mẫu giọng
                </label>
                <div className="grid grid-cols-2 gap-2 bg-secondary/40 p-1 rounded-xl border border-border">
                  <button
                    type="button"
                    onClick={() => setSaveVoiceIsPublic(false)}
                    className={`min-h-[42px] px-3 rounded-lg text-xs font-bold transition-colors cursor-pointer flex items-center justify-center gap-1.5 ${
                      !saveVoiceIsPublic
                        ? "bg-card text-foreground border border-border shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Lock className="w-3.5 h-3.5" />
                    <span>Riêng tư (Private)</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setSaveVoiceIsPublic(true)}
                    className={`min-h-[42px] px-3 rounded-lg text-xs font-bold transition-colors cursor-pointer flex items-center justify-center gap-1.5 ${
                      saveVoiceIsPublic
                        ? "bg-card text-foreground border border-border shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Globe className="w-3.5 h-3.5" />
                    <span>Công khai (Public)</span>
                  </button>
                </div>
              </div>
            </div>

            <div className="flex gap-2.5 justify-end mt-2 pt-3 border-t border-border">
              <button
                type="button"
                onClick={() => setIsSaveModalOpen(false)}
                className="min-h-[44px] px-4 py-2 bg-secondary hover:bg-secondary/80 border border-border text-xs font-semibold text-foreground rounded-xl cursor-pointer transition-colors"
              >
                Hủy
              </button>
              <button
                type="button"
                onClick={() => void handleSaveVoiceSubmit()}
                disabled={isSavingVoice || !saveVoiceName.trim() || !saveVoiceRefText.trim()}
                className="min-h-[44px] px-5 py-2 bg-primary hover:bg-primary-hover disabled:bg-muted disabled:text-muted-foreground text-xs font-bold text-primary-foreground rounded-xl cursor-pointer transition-colors"
              >
                {isSavingVoice ? "Đang lưu..." : "Xác nhận lưu vào Thư viện"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
