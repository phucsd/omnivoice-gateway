import React from "react";
import { Loader2, CheckCircle2, AlertCircle, Clock, Server, Zap } from "lucide-react";

interface JobStatusCardProps {
  jobId: string;
  status: string;
  message?: string | null;
  progress: number;
  errorMessage?: string | null;
  queueTime?: number | null;
  processingTime?: number | null;
  totalTime?: number | null;
  startedAt?: string | null;
}

const statusMap: Record<string, { label: string; color: string; icon: React.ComponentType<{ className?: string }>; spin?: boolean }> = {
  queued: {
    label: "Đang xếp hàng chờ",
    color: "text-muted-foreground bg-muted/40 border-border",
    icon: Clock,
    spin: false,
  },
  queued_kaggle: {
    label: "Kaggle đang xếp hàng chờ cấp GPU",
    color: "text-warning bg-warning/10 border-warning/20",
    icon: Loader2,
    spin: true,
  },
  preparing_input: {
    label: "Đang chuẩn bị dữ liệu đầu vào",
    color: "text-warning bg-warning/10 border-warning/20",
    icon: Loader2,
    spin: true,
  },
  starting_worker: {
    label: "Đang khởi tạo máy chủ xử lý giọng nói",
    color: "text-warning bg-warning/10 border-warning/20",
    icon: Loader2,
    spin: true,
  },
  booting_kaggle: {
    label: "Đang khởi động Kaggle Worker",
    color: "text-warning bg-warning/10 border-warning/20",
    icon: Loader2,
    spin: true,
  },
  installing_runtime: {
    label: "Đang cài đặt môi trường chạy",
    color: "text-warning bg-warning/10 border-warning/20",
    icon: Loader2,
    spin: true,
  },
  loading_model: {
    label: "Đang tải mô hình AI",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  running: {
    label: "Đang thực thi trên GPU",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  transcribing: {
    label: "Đang nhận dạng giọng nói (ASR)",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  transcribing_ref: {
    label: "Đang chuyển mã giọng tham chiếu",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  separating_audio: {
    label: "Đang tách giọng nói & nhạc nền",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  generating_preview: {
    label: "Đang tạo bản nghe thử",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  normalizing_sample: {
    label: "Đang chuẩn hóa âm thanh mẫu",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  cloning_voice: {
    label: "Đang xử lý âm thanh nhân bản",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  generating_audio: {
    label: "Đang tổng hợp âm thanh",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  generating_tts: {
    label: "Đang lồng tiếng phân đoạn",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  mixing_audio: {
    label: "Đang hòa trộn âm thanh",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  exporting_wav: {
    label: "Đang xuất tệp âm thanh WAV",
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: true,
  },
  awaiting_review: {
    label: "Chờ kiểm duyệt phụ đề",
    color: "text-warning bg-warning/10 border-warning/20",
    icon: Clock,
    spin: false,
  },
  completed: {
    label: "Hoàn tất",
    color: "text-success bg-success/10 border-success/20",
    icon: CheckCircle2,
    spin: false,
  },
  failed: {
    label: "Lỗi xử lý",
    color: "text-destructive bg-destructive/10 border-destructive/20",
    icon: AlertCircle,
    spin: false,
  },
  cancelled: {
    label: "Đã hủy",
    color: "text-muted-foreground bg-muted/40 border-border",
    icon: AlertCircle,
    spin: false,
  },
};

export const JobStatusCard: React.FC<JobStatusCardProps> = ({
  jobId,
  status,
  message,
  progress,
  errorMessage,
  queueTime,
  processingTime,
  totalTime,
  startedAt,
}) => {
  const currentStatus = statusMap[status] || {
    label: status,
    color: "text-primary bg-primary/10 border-primary/20",
    icon: Loader2,
    spin: status !== "completed" && status !== "failed" && status !== "cancelled",
  };

  const IconComponent = currentStatus.icon;
  const shouldSpin = Boolean(currentStatus.spin);
  const isTerminal = status === "completed" || status === "failed" || status === "cancelled";
  const isWaitingForWorker =
    !isTerminal &&
    !startedAt &&
    (status === "queued" || status === "starting_worker" || status === "queued_kaggle" || status === "booting_kaggle");
  const hasAnyTiming =
    typeof queueTime === "number" || typeof processingTime === "number" || typeof totalTime === "number";

  return (
    <div className="bg-card border border-border rounded-[20px] p-5 shadow-sm flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1 min-w-0">
          <span className="text-xs font-medium text-muted-foreground">MÃ YÊU CẦU</span>
          <span className="text-sm font-mono font-bold text-foreground truncate">{jobId}</span>
        </div>
        <div
          className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold border shrink-0 ${currentStatus.color}`}
        >
          <IconComponent className={`w-3.5 h-3.5 ${shouldSpin ? "animate-spin" : ""}`} />
          <span>{currentStatus.label}</span>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between text-xs font-semibold text-muted-foreground">
          <span>Tiến trình xử lý</span>
          <span className="font-mono">{progress}%</span>
        </div>
        <div className="w-full bg-muted h-2 rounded-full overflow-hidden">
          <div
            className={`h-full transition-all duration-500 rounded-full ${
              status === "failed"
                ? "bg-destructive"
                : status === "completed"
                ? "bg-success"
                : "bg-primary"
            }`}
            style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
          />
        </div>
      </div>

      {hasAnyTiming && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-1">
          <div className="bg-secondary/35 border border-border/70 rounded-xl px-3 py-2 flex flex-col gap-0.5">
            <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1">
              <Server className="w-3 h-3 text-warning" />
              <span>Khởi động Worker</span>
            </span>
            <div className="flex items-baseline gap-1.5">
              <span className="text-sm font-mono font-bold text-foreground">
                {typeof queueTime === "number" ? `${queueTime.toFixed(1)}s` : "0.0s"}
              </span>
              {!isWaitingForWorker && typeof queueTime === "number" && queueTime < 2.5 && (
                <span className="text-[10px] font-semibold text-success">(Sẵn sàng)</span>
              )}
              {isWaitingForWorker && (
                <span className="text-[10px] font-medium text-warning animate-pulse">đang cấp GPU...</span>
              )}
            </div>
          </div>

          <div className="bg-secondary/35 border border-border/70 rounded-xl px-3 py-2 flex flex-col gap-0.5">
            <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1">
              <Zap className="w-3 h-3 text-primary" />
              <span>Thời gian Gen Audio</span>
            </span>
            <div className="flex items-baseline gap-1.5">
              {typeof processingTime === "number" ? (
                <>
                  <span className="text-sm font-mono font-bold text-primary">
                    {processingTime.toFixed(1)}s
                  </span>
                  {!isTerminal && (
                    <span className="text-[10px] font-medium text-primary animate-pulse">đang tổng hợp...</span>
                  )}
                </>
              ) : (
                <span className="text-xs font-medium text-muted-foreground italic">
                  {isWaitingForWorker ? "Chờ worker..." : "—"}
                </span>
              )}
            </div>
          </div>

          <div className="bg-secondary/35 border border-border/70 rounded-xl px-3 py-2 flex flex-col gap-0.5">
            <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1">
              <Clock className="w-3 h-3 text-success" />
              <span>Tổng thời gian</span>
            </span>
            <span className="text-sm font-mono font-bold text-foreground">
              {typeof totalTime === "number" ? `${totalTime.toFixed(1)}s` : "0.0s"}
            </span>
          </div>
        </div>
      )}

      {(message || errorMessage) && (
        <div className="text-sm border-t border-border pt-3 mt-1 flex flex-col gap-1.5">
          {message && (
            <div className="text-foreground flex items-start gap-1.5">
              <span className="text-primary font-semibold">•</span>
              <p className="flex-grow">{message}</p>
            </div>
          )}
          {status === "failed" && errorMessage && (
            <div className="bg-destructive/5 border border-destructive/10 rounded-lg p-3 text-xs text-destructive font-mono overflow-auto max-h-40 whitespace-pre-wrap">
              <strong>Chi tiết lỗi:</strong>
              <p className="mt-1">{errorMessage}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
