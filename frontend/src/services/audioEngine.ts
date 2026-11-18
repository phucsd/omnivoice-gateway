// High-Performance Web Audio API Engine for Instant Playback

interface WindowWithWebkitAudio extends Window {
  webkitAudioContext?: typeof AudioContext;
}

class AudioEngineService {
  private audioCtx: AudioContext | null = null;
  private bufferCache = new Map<string, AudioBuffer>();
  private activeSourceNode: AudioBufferSourceNode | null = null;
  private gainNode: GainNode | null = null;

  private currentUrl: string | null = null;
  private startTime: number = 0;
  private startOffset: number = 0;
  private isPlaying: boolean = false;
  private volume: number = 0.8;

  private getContext(): AudioContext {
    if (!this.audioCtx) {
      const win = window as WindowWithWebkitAudio;
      const AudioCtxClass = window.AudioContext || win.webkitAudioContext;
      if (!AudioCtxClass) {
        throw new Error("Web Audio API is not supported in this browser.");
      }
      this.audioCtx = new AudioCtxClass();
      this.gainNode = this.audioCtx.createGain();
      this.gainNode.gain.value = this.volume;
      this.gainNode.connect(this.audioCtx.destination);
    }
    if (this.audioCtx.state === "suspended") {
      void this.audioCtx.resume();
    }
    return this.audioCtx;
  }

  /**
   * Pre-fetches audio data over HTTPS with Bearer auth when needed and decodes into AudioBuffer.
   */
  async preload(url: string): Promise<AudioBuffer | null> {
    if (!url) return null;
    if (this.bufferCache.has(url)) {
      return this.bufferCache.get(url) ?? null;
    }

    try {
      const ctx = this.getContext();
      const headers: HeadersInit = {};
      const token = localStorage.getItem("VITE_JWT_TOKEN");
      if (token && !url.startsWith("blob:") && !url.startsWith("data:") && !url.includes("huggingface.co/datasets")) {
        headers["Authorization"] = `Bearer ${token}`;
      }
      const response = await fetch(url, { headers });
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

      const arrayBuffer = await response.arrayBuffer();
      const decodedBuffer = await ctx.decodeAudioData(arrayBuffer);

      this.bufferCache.set(url, decodedBuffer);
      return decodedBuffer;
    } catch (err) {
      console.warn("[AudioEngine] Preload failed, fallback to element fetch:", err);
      return null;
    }
  }

  async play(
    url: string,
    offset: number = 0,
    onEnded?: () => void
  ): Promise<boolean> {
    if (!url) return false;
    const ctx = this.getContext();

    this.stop();

    let buffer = this.bufferCache.get(url);
    if (!buffer) {
      buffer = (await this.preload(url)) ?? undefined;
    }

    if (!buffer) {
      return false;
    }

    this.currentUrl = url;
    this.startOffset = offset;
    this.startTime = ctx.currentTime;

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gainNode!);

    source.onended = () => {
      if (this.currentUrl === url && this.isPlaying) {
        this.isPlaying = false;
        if (onEnded) onEnded();
      }
    };

    source.start(0, offset);
    this.activeSourceNode = source;
    this.isPlaying = true;

    return true;
  }

  pause(): number {
    if (!this.isPlaying || !this.audioCtx) return this.startOffset;

    const elapsed = this.audioCtx.currentTime - this.startTime;
    this.startOffset += elapsed;

    if (this.activeSourceNode) {
      try {
        this.activeSourceNode.stop();
      } catch (err) {
        console.debug("[AudioEngine] Stop during pause ignored:", err);
      }
      this.activeSourceNode = null;
    }

    this.isPlaying = false;
    return this.startOffset;
  }

  stop(): void {
    if (this.activeSourceNode) {
      try {
        this.activeSourceNode.stop();
      } catch (err) {
        console.debug("[AudioEngine] Stop ignored:", err);
      }
      this.activeSourceNode = null;
    }
    this.isPlaying = false;
    this.startOffset = 0;
  }

  setVolume(vol: number): void {
    this.volume = Math.max(0, Math.min(1, vol));
    if (this.gainNode) {
      this.gainNode.gain.value = this.volume;
    }
  }

  getIsPlaying(): boolean {
    return this.isPlaying;
  }

  getDuration(url: string): number {
    const buf = this.bufferCache.get(url);
    return buf ? buf.duration : 0;
  }
}

export const audioEngine = new AudioEngineService();
