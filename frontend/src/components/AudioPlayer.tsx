import React, { useState, useEffect, useRef } from "react";
import { Download, Play, Pause, Volume1, Volume2, VolumeX, X, RotateCcw, RotateCw, Loader2, AlertCircle, RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { audioEngine } from "../services/audioEngine";

interface AudioPlayerProps {
  url: string;
  title: string;
  isPlayingGlobal: boolean;
  onPlayingGlobalChange: (playing: boolean) => void;
  onClose: () => void;
}

export const AudioPlayer: React.FC<AudioPlayerProps> = ({
  url,
  title,
  isPlayingGlobal,
  onPlayingGlobalChange,
  onClose,
}) => {
  const audioRef = useRef<HTMLAudioElement>(null);
  const onPlayingChangeRef = useRef(onPlayingGlobalChange);
  useEffect(() => {
    onPlayingChangeRef.current = onPlayingGlobalChange;
  }, [onPlayingGlobalChange]);

  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [volumeVal, setVolumeVal] = useState(0.8);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1.0);
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);

  const [isLoading, setIsLoading] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [blobUrl, setBlobUrl] = useState<string>("");

  useEffect(() => {
    let isCancelled = false;
    let createdObjectUrl = "";

    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }

    if (!url) {
      Promise.resolve().then(() => {
        if (!isCancelled) {
          setBlobUrl("");
          setIsLoading(false);
          setHasError(false);
        }
      });
      return () => {
        isCancelled = true;
      };
    }

    Promise.resolve().then(() => {
      if (!isCancelled) {
        setIsLoading(true);
        setHasError(false);
      }
    });

    void audioEngine.preload(url);

    api
      .fetchProtectedAudioBlobUrl(url)
      .then(({ blobUrl: resolved, isObjectUrl }) => {
        if (isCancelled) {
          if (isObjectUrl) URL.revokeObjectURL(resolved);
          return;
        }
        if (isObjectUrl) {
          createdObjectUrl = resolved;
        }
        setBlobUrl(resolved);
        setIsLoading(false);
      })
      .catch((err: unknown) => {
        console.warn("Pre-fetch audio blob failed:", err);
        if (!isCancelled) {
          setBlobUrl("");
          setHasError(true);
          setIsLoading(false);
        }
      });

    return () => {
      isCancelled = true;
      if (createdObjectUrl) {
        URL.revokeObjectURL(createdObjectUrl);
      }
    };
  }, [url]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !blobUrl) return;

    audio
      .play()
      .then(() => {
        setIsPlaying(true);
        setHasError(false);
        onPlayingChangeRef.current(true);
      })
      .catch(() => {
        // Play abort or autoplay policy block is expected before explicit click
      });
  }, [blobUrl]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const handlePlay = () => {
      setIsPlaying(true);
      onPlayingChangeRef.current(true);
    };
    const handlePause = () => {
      setIsPlaying(false);
      onPlayingChangeRef.current(false);
    };
    const handleTimeUpdate = () => setCurrentTime(audio.currentTime);
    const handleLoadedMetadata = () => {
      setDuration(audio.duration);
      setIsLoading(false);
      setHasError(false);
    };
    const handleEnded = () => {
      setIsPlaying(false);
      onPlayingChangeRef.current(false);
      setCurrentTime(0);
    };
    const handleWaiting = () => setIsLoading(true);
    const handlePlaying = () => {
      setIsLoading(false);
      setHasError(false);
    };
    const handleCanPlay = () => {
      setIsLoading(false);
      setHasError(false);
    };
    const handleError = () => {
      setIsLoading(false);
      if (blobUrl) {
        setHasError(true);
      }
    };

    audio.addEventListener("play", handlePlay);
    audio.addEventListener("pause", handlePause);
    audio.addEventListener("timeupdate", handleTimeUpdate);
    audio.addEventListener("loadedmetadata", handleLoadedMetadata);
    audio.addEventListener("ended", handleEnded);
    audio.addEventListener("waiting", handleWaiting);
    audio.addEventListener("playing", handlePlaying);
    audio.addEventListener("canplay", handleCanPlay);
    audio.addEventListener("error", handleError);

    return () => {
      audio.removeEventListener("play", handlePlay);
      audio.removeEventListener("pause", handlePause);
      audio.removeEventListener("timeupdate", handleTimeUpdate);
      audio.removeEventListener("loadedmetadata", handleLoadedMetadata);
      audio.removeEventListener("ended", handleEnded);
      audio.removeEventListener("waiting", handleWaiting);
      audio.removeEventListener("playing", handlePlaying);
      audio.removeEventListener("canplay", handleCanPlay);
      audio.removeEventListener("error", handleError);
    };
  }, [blobUrl]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !blobUrl) return;

    if (isPlayingGlobal) {
      if (audio.paused) {
        audio.play().catch((err) => console.error("Global play error:", err));
      }
    } else if (!audio.paused) {
      audio.pause();
    }
  }, [isPlayingGlobal, blobUrl]);

  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.volume = isMuted ? 0 : volumeVal;
    }
  }, [volumeVal, isMuted]);

  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.playbackRate = playbackRate;
    }
  }, [playbackRate]);

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio || !blobUrl) return;
    if (isPlaying) {
      audio.pause();
    } else {
      audio.play().catch((err) => console.error("Audio playback error:", err));
    }
  };

  const handleSeekChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const audio = audioRef.current;
    if (!audio) return;
    const newTime = parseFloat(e.target.value);
    audio.currentTime = newTime;
    setCurrentTime(newTime);
  };

  const toggleMute = () => {
    setIsMuted(!isMuted);
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newVal = parseFloat(e.target.value);
    setVolumeVal(newVal);
    if (newVal > 0 && isMuted) {
      setIsMuted(false);
    }
  };

  const handleDownload = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (!url) return;
    try {
      const targetHref = blobUrl || (await api.fetchProtectedAudioBlobUrl(url)).blobUrl;
      const shouldRevoke = !blobUrl && targetHref.startsWith("blob:");
      const link = document.createElement("a");
      link.href = targetHref;
      link.download = title ? `${title.replace(/\s+/g, "_")}.wav` : "audio.wav";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      if (shouldRevoke) {
        setTimeout(() => URL.revokeObjectURL(targetHref), 2000);
      }
    } catch (err) {
      console.error("Audio download failed:", err);
    }
  };

  const handleRewind = () => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(0, audio.currentTime - 10);
    setCurrentTime(audio.currentTime);
  };

  const handleForward = () => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.min(duration, audio.currentTime + 10);
    setCurrentTime(audio.currentTime);
  };

  const formatTime = (time: number) => {
    if (isNaN(time)) return "00:00";
    const minutes = Math.floor(time / 60);
    const seconds = Math.floor(time % 60);
    const mm = minutes < 10 ? `0${minutes}` : `${minutes}`;
    const ss = seconds < 10 ? `0${seconds}` : `${seconds}`;
    return `${mm}:${ss}`;
  };

  const speeds = [0.75, 1.0, 1.25, 1.5, 2.0];
  const percentage = duration ? (currentTime / duration) * 100 : 0;

  const renderVolumeIcon = () => {
    if (isMuted || volumeVal === 0) {
      return <VolumeX className="w-4 h-4" />;
    }
    if (volumeVal < 0.5) {
      return <Volume1 className="w-4 h-4" />;
    }
    return <Volume2 className="w-4 h-4" />;
  };

  if (!url) {
    return (
      <div className="w-full h-full flex items-center justify-center text-sm text-muted-foreground font-medium relative px-4">
        <div className="flex items-center gap-2">
          <Volume2 className="w-4 h-4 text-muted-foreground shrink-0" />
          <span className="truncate">Chưa có audio được phát. Chọn một bản ghi để nghe.</span>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-full relative">
      <div className="h-full px-3 sm:px-6 flex items-center justify-between gap-3 sm:gap-4 relative z-10">
        <audio ref={audioRef} src={blobUrl || undefined} preload="auto" />

        {/* Left: Controls & Metadata */}
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={handleRewind}
              aria-label="Tua lại 10 giây"
              className="min-w-[44px] min-h-[44px] hover:bg-secondary rounded-xl text-muted-foreground hover:text-foreground transition-colors cursor-pointer hidden xs:flex items-center justify-center shrink-0"
              title="Tua lại 10 giây"
            >
              <RotateCcw className="w-4 h-4" />
            </button>

            <button
              type="button"
              onClick={togglePlay}
              aria-label={isPlaying ? "Tạm dừng âm thanh" : "Phát âm thanh"}
              className="w-11 h-11 rounded-full bg-primary hover:bg-primary-hover text-primary-foreground flex items-center justify-center cursor-pointer shrink-0 transition-colors active:translate-y-[1px]"
              title={isPlaying ? "Tạm dừng" : "Phát"}
              disabled={hasError || isLoading}
            >
              {isLoading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : isPlaying ? (
                <Pause className="w-4 h-4 fill-current" />
              ) : (
                <Play className="w-4 h-4 fill-current ml-0.5" />
              )}
            </button>

            <button
              type="button"
              onClick={handleForward}
              aria-label="Tua tới 10 giây"
              className="min-w-[44px] min-h-[44px] hover:bg-secondary rounded-xl text-muted-foreground hover:text-foreground transition-colors cursor-pointer hidden xs:flex items-center justify-center shrink-0"
              title="Tua tới 10 giây"
            >
              <RotateCw className="w-4 h-4" />
            </button>
          </div>

          <div className="flex flex-col min-w-0 leading-tight">
            <span className="text-sm font-semibold text-foreground truncate max-w-[120px] sm:max-w-[180px]" title={title}>
              {title}
            </span>
            {hasError ? (
              <span className="text-xs text-destructive font-semibold flex items-center gap-1">
                <AlertCircle className="w-3.5 h-3.5" />
                <span>Lỗi phát âm thanh</span>
              </span>
            ) : (
              <span className="text-xs font-mono font-medium text-muted-foreground mt-0.5 tabular-nums">
                {formatTime(currentTime)} / {formatTime(duration)}
              </span>
            )}
          </div>
        </div>

        {/* Center: Seekbar */}
        <div className="flex-grow max-w-[400px] hidden sm:flex items-center">
          <input
            type="range"
            aria-label="Thanh tua âm thanh"
            min={0}
            max={duration || 100}
            value={currentTime}
            onChange={handleSeekChange}
            className="seekbar w-full"
            disabled={hasError}
            style={{
              background: `linear-gradient(to right, var(--primary) 0%, var(--primary) ${percentage}%, var(--slider-track) ${percentage}%, var(--slider-track) 100%)`
            }}
          />
        </div>

        {/* Right: Tools & Dismiss */}
        <div className="flex items-center gap-1 sm:gap-2 shrink-0">
          <div className="relative flex items-center">
            <button
              type="button"
              onClick={() => setShowSpeedMenu(!showSpeedMenu)}
              aria-label="Tốc độ phát"
              className="min-h-[44px] px-2.5 py-1.5 bg-background hover:bg-secondary border border-border rounded-xl text-xs font-semibold text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            >
              {playbackRate.toFixed(2)}x
            </button>

            {showSpeedMenu && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowSpeedMenu(false)} />
                <div className="absolute bottom-full right-0 mb-2.5 z-25 bg-card border border-border rounded-xl p-1.5 shadow-lg flex flex-col gap-0.5 min-w-[75px]">
                  {speeds.map((s) => (
                    <button
                      type="button"
                      key={s}
                      onClick={() => {
                        setPlaybackRate(s);
                        setShowSpeedMenu(false);
                      }}
                      className={`min-h-[36px] px-3 py-1.5 text-left rounded-lg text-xs font-semibold transition-colors cursor-pointer w-full ${
                        playbackRate === s
                          ? "bg-primary text-primary-foreground"
                          : "text-muted-foreground hover:text-foreground hover:bg-secondary"
                      }`}
                    >
                      {s.toFixed(2)}x
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          <div className="flex items-center gap-1.5 group/volume relative py-1">
            <button
              type="button"
              onClick={toggleMute}
              aria-label={isMuted ? "Bật âm thanh" : "Tắt âm thanh"}
              className="min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-secondary rounded-xl transition-colors cursor-pointer shrink-0"
              title={isMuted ? "Bật âm thanh" : "Tắt âm thanh"}
            >
              {renderVolumeIcon()}
            </button>

            <input
              type="range"
              aria-label="Âm lượng"
              min={0}
              max={1}
              step={0.05}
              value={isMuted ? 0 : volumeVal}
              onChange={handleVolumeChange}
              className="w-0 opacity-0 group-hover/volume:w-16 group-hover/volume:opacity-100 focus:w-16 focus:opacity-100 transition-all duration-200 h-1 bg-slider-track rounded-full appearance-none cursor-pointer range-xs accent-primary pointer-events-none group-hover/volume:pointer-events-auto focus:pointer-events-auto"
              style={{
                background: `linear-gradient(to right, var(--primary) 0%, var(--primary) ${(isMuted ? 0 : volumeVal) * 100}%, var(--slider-track) ${(isMuted ? 0 : volumeVal) * 100}%, var(--slider-track) 100%)`
              }}
            />
          </div>

          <button
            type="button"
            onClick={handleDownload}
            aria-label="Tải xuống tệp WAV"
            className="min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-secondary rounded-xl transition-colors cursor-pointer shrink-0"
            title="Tải xuống tệp WAV"
          >
            <Download className="w-4 h-4" />
          </button>

          <button
            type="button"
            onClick={() => {
              window.dispatchEvent(new CustomEvent("omnivoice:regenerate"));
            }}
            aria-label="Tạo lại âm thanh"
            className="min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-secondary rounded-xl transition-colors cursor-pointer shrink-0"
            title="Tạo lại âm thanh (Regenerate)"
          >
            <RefreshCw className="w-4 h-4" />
          </button>

          <div className="h-5 w-[1px] bg-border mx-0.5 shrink-0" />

          <button
            type="button"
            onClick={onClose}
            aria-label="Đóng trình phát âm thanh"
            className="min-w-[44px] min-h-[44px] flex items-center justify-center text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded-xl transition-colors cursor-pointer shrink-0"
            title="Đóng trình phát"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};
