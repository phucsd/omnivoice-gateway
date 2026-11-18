import React, { useState, useEffect, useRef } from "react";
import { Radio, ShieldAlert, CheckCircle2, ArrowRight, ExternalLink, KeyRound, Mail, UserPlus, LogIn } from "lucide-react";
import { api, getErrorMessage } from "../api/client";

const processedOAuthCodes = new Set<string>();

interface LoginRegisterProps {
  onLoginSuccess: (token: string) => void;
}

type AuthMode = "login" | "register" | "verify";

const GoogleIcon = ({ className = "w-5 h-5" }: { className?: string }) => (
  <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" />
    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
  </svg>
);

export const LoginRegister: React.FC<LoginRegisterProps> = ({ onLoginSuccess }) => {
  const apiBaseUrl = api.getApiBaseUrl();
  const isInIframe = typeof window !== "undefined" && window.self !== window.top;

  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const onLoginSuccessRef = useRef(onLoginSuccess);
  useEffect(() => {
    onLoginSuccessRef.current = onLoginSuccess;
  }, [onLoginSuccess]);

  // Handle Google OAuth Callback (code + signed state exchange) without cancelling on parent re-render
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const oauthError = urlParams.get("error");
    const code = urlParams.get("code");

    if (oauthError && !code) {
      window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
      void Promise.resolve().then(() => {
        setErrorMsg(`Đăng nhập Google không thành công (${oauthError}).`);
      });
      return;
    }

    if (!code || processedOAuthCodes.has(code)) return;
    processedOAuthCodes.add(code);

    const returnedState = urlParams.get("state") || sessionStorage.getItem("ov_oauth_state") || "";
    sessionStorage.removeItem("ov_oauth_state");
    const redirectUri = window.location.origin + window.location.pathname;
    window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);

    void Promise.resolve().then(async () => {
      setLoading(true);
      setErrorMsg(null);
      try {
        const res = await api.oauthCallback("google", code, redirectUri, returnedState);
        localStorage.setItem("VITE_JWT_TOKEN", res.access_token);
        onLoginSuccessRef.current(res.access_token);
      } catch (err: unknown) {
        processedOAuthCodes.delete(code);
        const msg = getErrorMessage(err, "Lỗi xác thực OAuth từ Google.");
        if (msg.includes("chưa được duyệt") || msg.includes("chưa xác thực")) {
          setErrorMsg("Tài khoản Google của bạn đã được đăng ký nhưng hiện đang ở trạng thái chờ Admin phê duyệt.");
        } else {
          setErrorMsg(msg);
        }
      } finally {
        setLoading(false);
      }
    });
  }, []);

  const handleGoogleLogin = async () => {
    setErrorMsg(null);
    setSuccessMsg(null);
    setLoading(true);

    const redirectUri = window.location.origin + window.location.pathname;
    let navigating = false;
    try {
      const res = await api.oauthLogin("google", redirectUri);
      if (res && res.auth_url) {
        if (res.state) {
          sessionStorage.setItem("ov_oauth_state", res.state);
        }
        if (isInIframe) {
          window.open(res.auth_url, "_blank", "noopener,noreferrer");
          setSuccessMsg("Đã mở trang đăng nhập Google ở cửa sổ mới do Space đang chạy trong iframe.");
        } else {
          navigating = true;
          window.location.href = res.auth_url;
          return;
        }
      } else {
        setErrorMsg("Không thể nhận liên kết đăng nhập Google từ hệ thống.");
      }
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, "Không thể khởi tạo đăng nhập Google."));
    } finally {
      if (!navigating) {
        setLoading(false);
      }
    }
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setSuccessMsg(null);
    setLoading(true);

    try {
      if (mode === "login") {
        const res = await api.login(username.trim(), password);
        localStorage.setItem("VITE_JWT_TOKEN", res.access_token);
        onLoginSuccess(res.access_token);
      } else if (mode === "register") {
        const res = await api.register(username.trim(), password, email.trim());
        const suffix = res.debug_code ? ` (Mã OTP thử nghiệm: ${res.debug_code})` : "";
        setSuccessMsg((res.message || "Đăng ký thành công. Kiểm tra email để lấy mã OTP.") + suffix);
        if (res.debug_code) {
          setOtpCode(res.debug_code);
        }
        setMode("verify");
      } else if (mode === "verify") {
        const res = await api.verifyEmail(username.trim(), otpCode.trim());
        setSuccessMsg(res.message || "Xác thực email thành công! Bạn có thể đăng nhập ngay.");
        setMode("login");
      }
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, "Thao tác xác thực thất bại."));
    } finally {
      setLoading(false);
    }
  };

  const handleResendOtp = async () => {
    if (!username.trim()) {
      setErrorMsg("Vui lòng nhập tên tài khoản trước khi gửi lại mã OTP.");
      return;
    }
    setErrorMsg(null);
    setSuccessMsg(null);
    setLoading(true);
    try {
      const res = await api.resendCode(username.trim());
      setSuccessMsg(res.message || "Đã gửi lại mã OTP.");
    } catch (err: unknown) {
      setErrorMsg(getErrorMessage(err, "Không thể gửi lại mã OTP."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background text-foreground flex items-center justify-center p-4 sm:p-6 font-sans">
      <div className="w-full max-w-md bg-card border border-border rounded-[20px] p-6 sm:p-8 flex flex-col gap-5 shadow-sm">
        {/* Header / Brand */}
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="bg-primary/10 p-3 rounded-xl border border-primary/20">
            <Radio className="w-7 h-7 text-primary" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            OmniVoice Gateway
          </h1>
          <p className="text-xs text-muted-foreground">
            Nền tảng tổng hợp &amp; nhân bản giọng nói AI chất lượng cao
          </p>
        </div>

        {/* Mode Switcher */}
        <div className="grid grid-cols-3 gap-1 p-1 bg-muted rounded-xl border border-border" role="tablist" aria-label="Chế độ xác thực">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "login"}
            onClick={() => { setMode("login"); setErrorMsg(null); setSuccessMsg(null); }}
            className={`min-h-[44px] px-2 py-2 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors cursor-pointer ${
              mode === "login"
                ? "bg-card text-foreground border border-border shadow-xs"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <LogIn className="w-3.5 h-3.5 shrink-0" />
            <span>Đăng nhập</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "register"}
            onClick={() => { setMode("register"); setErrorMsg(null); setSuccessMsg(null); }}
            className={`min-h-[44px] px-2 py-2 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors cursor-pointer ${
              mode === "register"
                ? "bg-card text-foreground border border-border shadow-xs"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <UserPlus className="w-3.5 h-3.5 shrink-0" />
            <span>Đăng ký</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "verify"}
            onClick={() => { setMode("verify"); setErrorMsg(null); setSuccessMsg(null); }}
            className={`min-h-[44px] px-2 py-2 rounded-lg text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors cursor-pointer ${
              mode === "verify"
                ? "bg-card text-foreground border border-border shadow-xs"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <Mail className="w-3.5 h-3.5 shrink-0" />
            <span>Xác thực</span>
          </button>
        </div>

        {/* Status messages */}
        {errorMsg && (
          <div role="alert" className="p-3.5 bg-destructive/10 border border-destructive/20 text-destructive rounded-xl text-xs leading-relaxed flex items-start gap-2">
            <ShieldAlert className="w-4 h-4 text-destructive shrink-0 mt-0.5" />
            <span>{errorMsg}</span>
          </div>
        )}
        {successMsg && (
          <div role="status" className="p-3.5 bg-success/10 border border-success/20 text-success rounded-xl text-xs leading-relaxed flex items-start gap-2">
            <CheckCircle2 className="w-4 h-4 text-success shrink-0 mt-0.5" />
            <span>{successMsg}</span>
          </div>
        )}

        {/* Username / Password / OTP Form */}
        <form onSubmit={handlePasswordSubmit} className="flex flex-col gap-3.5">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="auth-username" className="text-xs font-semibold text-foreground">
              Tên tài khoản
            </label>
            <input
              id="auth-username"
              type="text"
              required
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Nhập tên tài khoản"
              className="min-h-[44px] w-full px-3.5 py-2.5 bg-background border border-input rounded-xl text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
            />
          </div>

          {mode === "register" && (
            <div className="flex flex-col gap-1.5">
              <label htmlFor="auth-email" className="text-xs font-semibold text-foreground">
                Địa chỉ Email
              </label>
              <input
                id="auth-email"
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="min-h-[44px] w-full px-3.5 py-2.5 bg-background border border-input rounded-xl text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
              />
            </div>
          )}

          {mode !== "verify" ? (
            <div className="flex flex-col gap-1.5">
              <label htmlFor="auth-password" className="text-xs font-semibold text-foreground">
                Mật khẩu
              </label>
              <input
                id="auth-password"
                type="password"
                required
                minLength={mode === "register" ? 6 : 1}
                autoComplete={mode === "register" ? "new-password" : "current-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="min-h-[44px] w-full px-3.5 py-2.5 bg-background border border-input rounded-xl text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
              />
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <label htmlFor="auth-otp" className="text-xs font-semibold text-foreground">
                  Mã OTP xác thực (6 chữ số)
                </label>
                <button
                  type="button"
                  onClick={handleResendOtp}
                  disabled={loading}
                  className="text-xs font-semibold text-primary hover:text-primary-hover cursor-pointer"
                >
                  Gửi lại mã
                </button>
              </div>
              <input
                id="auth-otp"
                type="text"
                required
                value={otpCode}
                onChange={(e) => setOtpCode(e.target.value)}
                placeholder="123456"
                className="min-h-[44px] w-full px-3.5 py-2.5 bg-background border border-input rounded-xl text-sm font-mono tracking-widest text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
              />
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="min-h-[44px] w-full py-2.5 px-4 rounded-xl bg-primary hover:bg-primary-hover text-primary-foreground font-semibold text-sm transition-colors flex items-center justify-center gap-2 cursor-pointer active:translate-y-[1px] disabled:opacity-60 disabled:cursor-not-allowed"
          >
            <KeyRound className="w-4 h-4 shrink-0" />
            <span>
              {loading
                ? "Đang xử lý..."
                : mode === "login"
                  ? "Đăng nhập"
                  : mode === "register"
                    ? "Tạo tài khoản"
                    : "Xác thực Email"}
            </span>
          </button>
        </form>

        <div className="relative flex items-center py-1">
          <div className="flex-grow border-t border-border" />
          <span className="shrink mx-3 text-xs text-muted-foreground font-medium">Hoặc</span>
          <div className="flex-grow border-t border-border" />
        </div>

        {/* Google Sign-In */}
        <button
          type="button"
          onClick={handleGoogleLogin}
          disabled={loading}
          className="min-h-[44px] w-full py-2.5 px-4 rounded-xl font-semibold text-sm transition-colors flex items-center justify-center gap-2.5 cursor-pointer border border-border bg-secondary hover:bg-secondary/80 text-foreground active:translate-y-[1px] disabled:opacity-60 disabled:cursor-not-allowed"
        >
          <GoogleIcon className="w-5 h-5 shrink-0" />
          <span>{isInIframe ? "Đăng nhập Google (mở tab mới)" : "Tiếp tục với Google"}</span>
          <ArrowRight className="w-4 h-4 ml-auto text-muted-foreground" />
        </button>

        {isInIframe && (
          <a
            href={window.location.href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted-foreground hover:text-foreground flex items-center justify-center gap-1.5 underline underline-offset-4"
          >
            <span>Mở ứng dụng toàn màn hình (tab mới)</span>
            <ExternalLink className="w-3.5 h-3.5 shrink-0" />
          </a>
        )}

        {/* Public Docs & Gateway Info */}
        <div className="pt-3 border-t border-border flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <a
            href="#/docs"
            className="min-h-[44px] inline-flex items-center gap-1.5 font-semibold text-primary hover:text-primary-hover hover:underline"
          >
            <span>Tài liệu API công khai</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </a>
          <span className="font-mono text-xs text-foreground bg-muted px-2.5 py-1 rounded-lg truncate max-w-[200px]" title={apiBaseUrl}>
            {apiBaseUrl}
          </span>
        </div>
      </div>
    </div>
  );
};
