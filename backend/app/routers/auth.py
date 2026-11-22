import hashlib
import hmac
import re
import secrets
import string
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode, urlparse

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User
from app.utils.auth import (
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)
from app.utils.ids import generate_id
from app.utils.mail import send_verification_email
from app.utils.rate_limit import enforce_auth_rate_limit

router = APIRouter(prefix="/v1/auth", tags=["Authentication"])

# Helper email validator regex
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

class UserRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    email: str = Field(..., max_length=150)

class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)

class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key: str
    created_at: datetime
    last_used_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class UserLoginRequest(BaseModel):
    username: str
    password: str

class VerifyEmailRequest(BaseModel):
    username: str
    code: str

class ResendCodeRequest(BaseModel):
    username: str

class MockOAuthRequest(BaseModel):
    email: str
    username: str
    oauth_provider: str
    oauth_id: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserMeResponse(BaseModel):
    id: str
    username: str
    email: Optional[str] = None
    is_verified: bool
    is_admin: bool
    has_api_key: bool
    api_key: Optional[str] = None
    created_at: datetime

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: UserRegisterRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    enforce_auth_rate_limit(request, "register", limit=5, window_seconds=300)
    # Check if registration feature is enabled
    from app.models import SystemSetting
    allow_reg = db.query(SystemSetting).filter(SystemSetting.key == "allow_registration").first()
    is_reg_allowed = (
        allow_reg.value.strip().lower() != "false"
        if (allow_reg and allow_reg.value.strip())
        else bool(settings.ALLOW_REGISTRATION)
    )
    if not is_reg_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tính năng đăng ký tài khoản mới hiện đang bị tạm khóa bởi Quản trị viên."
        )

    # Validate email format
    if not EMAIL_REGEX.match(payload.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Định dạng email không hợp lệ."
        )

    # Check if username exists
    existing_user = db.query(User).filter(User.username == payload.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tên tài khoản đã tồn tại trên hệ thống."
        )
        
    # Check if email exists
    existing_email = db.query(User).filter(User.email == payload.email).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Địa chỉ email đã được đăng ký."
        )
        
    hashed_pwd = get_password_hash(payload.password)
    user_id = generate_id("usr")
    
    # Auto-generate API Key on registration
    api_key = f"ovg_live_{secrets.token_hex(24)}"
    
    # Generate cryptographically strong 6-digit verification code
    otp_code = "".join(secrets.choice(string.digits) for _ in range(6))
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    
    from app.models import SystemSetting
    req_approval = db.query(SystemSetting).filter(SystemSetting.key == "require_admin_approval").first()
    is_approved = False if (req_approval and req_approval.value.strip().lower() == "true") else True

    new_user = User(
        id=user_id,
        username=payload.username,
        email=payload.email,
        hashed_password=hashed_pwd,
        is_verified=False,
        verification_code=otp_code,
        verification_expires_at=expires_at,
        is_admin=False,
        is_approved=is_approved,
        api_key=api_key
    )
    
    db.add(new_user)
    db.commit()
    
    # Send email in background
    background_tasks.add_task(send_verification_email, payload.email, payload.username, otp_code)
    
    response_data = {
        "status": "success", 
        "message": "Đăng ký thành công. Vui lòng kiểm tra email để lấy mã xác thực kích hoạt tài khoản."
    }
    
    # Expose debug_code ONLY in automated testing environments
    if (settings.APP_ENV or "").lower() == "testing":
        response_data["debug_code"] = otp_code

    return response_data


@router.post("/verify-email", response_model=dict)
def verify_email(payload: VerifyEmailRequest, request: Request, db: Session = Depends(get_db)):
    enforce_auth_rate_limit(request, "verify_email", limit=10, window_seconds=300, extra_key=payload.username)
    user = db.query(User).filter(User.username == payload.username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy người dùng."
        )

    if user.is_verified:
        return {"status": "success", "message": "Tài khoản đã được xác thực trước đó."}

    if not user.verification_code or not hmac.compare_digest(user.verification_code, payload.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mã xác thực không hợp lệ."
        )

    if user.verification_expires_at and user.verification_expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mã xác thực đã hết hạn. Vui lòng yêu cầu mã mới."
        )

    user.is_verified = True
    user.verification_code = None
    user.verification_expires_at = None
    db.commit()

    if not user.is_approved:
        return {
            "status": "pending_approval",
            "message": "Xác thực email thành công! Tài khoản của bạn đang chờ Quản trị viên phê duyệt trước khi có thể đăng nhập.",
        }

    return {"status": "success", "message": "Xác thực email thành công! Tài khoản đã được kích hoạt."}


@router.post("/resend-code", response_model=dict)
def resend_code(
    payload: ResendCodeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    enforce_auth_rate_limit(request, "resend_code", limit=5, window_seconds=300, extra_key=payload.username)
    user = db.query(User).filter(User.username == payload.username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy người dùng."
        )

    if user.is_verified:
        return {"status": "success", "message": "Tài khoản đã được xác thực trước đó."}

    otp_code = "".join(secrets.choice(string.digits) for _ in range(6))
    expires_at = datetime.utcnow() + timedelta(minutes=10)

    user.verification_code = otp_code
    user.verification_expires_at = expires_at
    db.commit()

    background_tasks.add_task(send_verification_email, user.email, user.username, otp_code)

    response_data = {
        "status": "success",
        "message": "Đã gửi lại mã xác thực mới vào email của bạn."
    }

    if (settings.APP_ENV or "").lower() == "testing":
        response_data["debug_code"] = otp_code

    return response_data


@router.post("/login", response_model=TokenResponse)
def login(payload: UserLoginRequest, request: Request, db: Session = Depends(get_db)):
    enforce_auth_rate_limit(request, "login", limit=10, window_seconds=300, extra_key=payload.username)
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản hoặc mật khẩu không chính xác."
        )
        
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản chưa được xác thực email. Vui lòng nhập mã OTP để kích hoạt."
        )
        
    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản của bạn chưa được duyệt hoặc đã bị khóa bởi Admin."
        )
        
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", response_model=UserMeResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return UserMeResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        is_verified=current_user.is_verified,
        is_admin=current_user.is_admin,
        has_api_key=current_user.api_key is not None,
        api_key=current_user.api_key,
        created_at=current_user.created_at
    )

@router.post("/apikey", response_model=dict)
def generate_api_key(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    new_key = f"ovg_live_{secrets.token_hex(24)}"
    current_user.api_key = new_key
    db.commit()
    return {
        "status": "success",
        "message": "Tạo khóa API mới thành công.",
        "api_key": new_key
    }

@router.delete("/apikey", response_model=dict)
def revoke_api_key(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user.api_key = None
    db.commit()
    return {
        "status": "success",
        "message": "Đã thu hồi khóa API thành công."
    }

@router.get("/apikeys", response_model=list[ApiKeyResponse])
def list_user_api_keys(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Retrieves all API Keys for the authenticated user."""
    from app.models import ApiKey
    keys = db.query(ApiKey).filter(ApiKey.user_id == current_user.id).order_by(ApiKey.created_at.desc()).all()
    return keys

@router.post("/apikeys", response_model=ApiKeyResponse)
def create_user_api_key(payload: ApiKeyCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Generates a new named API Key for the authenticated user."""
    from app.models import ApiKey
    from app.utils.ids import generate_id
    
    new_key = f"ovg_live_{secrets.token_hex(24)}"
    key_entry = ApiKey(
        id=generate_id("ak"),
        user_id=current_user.id,
        name=payload.name,
        key=new_key
    )
    db.add(key_entry)
    db.commit()
    db.refresh(key_entry)
    return key_entry

@router.delete("/apikeys/{key_id}", response_model=dict)
def delete_user_api_key(key_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Revokes a specific API Key for the authenticated user."""
    from app.models import ApiKey
    key_entry = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.user_id == current_user.id).first()
    if not key_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy Khóa API hoặc bạn không có quyền sở hữu."
        )
    db.delete(key_entry)
    db.commit()
    return {"status": "success", "message": "Thu hồi khóa API thành công."}

# --- OAuth Helpers & Login (Google Only) ---


def _validate_oauth_redirect_uri(redirect_uri: str, request: Request) -> str:
    """Allow OAuth redirect only to trusted origins matching gateway/CORS configuration."""
    raw = (redirect_uri or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri không hợp lệ.",
        )

    target_host = parsed.netloc.lower()
    allowed_hosts = {urlparse(str(request.base_url)).netloc.lower()}
    for origin in settings.cors_origins:
        netloc = urlparse(origin).netloc.lower()
        if netloc:
            allowed_hosts.add(netloc)
    for configured in (settings.PUBLIC_API_BASE_URL, settings.API_BASE_URL):
        if configured:
            netloc = urlparse(configured).netloc.lower()
            if netloc:
                allowed_hosts.add(netloc)

    hostname_only = (parsed.hostname or "").lower()
    is_trusted_suffix = (
        parsed.scheme == "https"
        and (
            hostname_only.endswith(".hf.space")
            or hostname_only == "omnivoice-gateway.pages.dev"
            or hostname_only.endswith(".omnivoice-gateway.pages.dev")
        )
    )
    is_local_dev = hostname_only in ("localhost", "127.0.0.1")

    if target_host not in allowed_hosts and not is_trusted_suffix and not is_local_dev:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="redirect_uri không nằm trong danh sách tên miền được phép.",
        )
    return raw


def _sign_oauth_state(redirect_uri: str) -> str:
    ts = str(int(time.time()))
    nonce = secrets.token_urlsafe(16)
    msg = f"{ts}.{nonce}.{redirect_uri}".encode("utf-8")
    sig = hmac.new(settings.SECRET_KEY.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]
    return f"{ts}.{nonce}.{sig}"


def _verify_oauth_state(state: Optional[str], redirect_uri: str) -> None:
    if not state or state.count(".") != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Thiếu hoặc sai chữ ký OAuth state (CSRF protection).",
        )
    ts_str, nonce, sig = state.split(".", 2)
    try:
        ts_val = int(ts_str)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state không hợp lệ.",
        ) from exc

    now = int(time.time())
    if now - ts_val > 600 or ts_val - now > 60:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phiên đăng nhập Google đã hết hạn. Vui lòng thử lại.",
        )

    expected = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"{ts_str}.{nonce}.{redirect_uri}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Xác thực OAuth state thất bại.",
        )


@router.post("/oauth/mock", response_model=TokenResponse)
def oauth_mock(payload: MockOAuthRequest, db: Session = Depends(get_db)):
    """Mock OAuth login endpoint strictly limited to automated testing."""
    if (settings.APP_ENV or "").lower() != "testing":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Mock OAuth endpoint is disabled outside automated tests.",
        )
    if payload.oauth_provider != "google":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hệ thống hiện tại chỉ hỗ trợ đăng nhập qua tài khoản Google."
        )

    # Check requirement for admin approval setting
    from app.models import SystemSetting
    req_approval = db.query(SystemSetting).filter(SystemSetting.key == "require_admin_approval").first()
    system_requires_approval = (req_approval and req_approval.value.strip().lower() == "true")

    # Look for existing user by oauth provider + id
    user = db.query(User).filter(
        User.oauth_provider == payload.oauth_provider,
        User.oauth_id == payload.oauth_id
    ).first()
    
    if not user:
        # Also check by email to merge/associate
        user = db.query(User).filter(User.email == payload.email).first()
        if user:
            # Link OAuth details
            user.oauth_provider = payload.oauth_provider
            user.oauth_id = payload.oauth_id
            if payload.email == "phucsd@gmail.com":
                user.is_admin = True
                user.is_approved = True
                user.is_verified = True
            db.commit()
        else:
            is_admin_user = (payload.email == "phucsd@gmail.com")
            if not is_admin_user:
                allow_reg = db.query(SystemSetting).filter(SystemSetting.key == "allow_registration").first()
                is_reg_allowed = (
                    allow_reg.value.strip().lower() != "false"
                    if (allow_reg and allow_reg.value.strip())
                    else bool(settings.ALLOW_REGISTRATION)
                )
                if not is_reg_allowed:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Tính năng đăng ký tài khoản mới hiện đang bị tạm khóa bởi Quản trị viên.",
                    )
            # Auto-register new OAuth user
            user_id = generate_id("usr")
            rand_pwd = secrets.token_hex(16)
            hashed_pwd = get_password_hash(rand_pwd)
            api_key = f"ovg_live_{secrets.token_hex(24)}"
            
            # Ensure unique username
            base_username = payload.username
            username = base_username
            counter = 1
            while db.query(User).filter(User.username == username).first():
                username = f"{base_username}_{counter}"
                counter += 1
                
            is_approved = True if (is_admin_user or not system_requires_approval) else False

            user = User(
                id=user_id,
                username=username,
                email=payload.email,
                hashed_password=hashed_pwd,
                is_verified=True,  # OAuth emails are pre-verified
                is_admin=is_admin_user,
                is_approved=is_approved,
                oauth_provider=payload.oauth_provider,
                oauth_id=payload.oauth_id,
                api_key=api_key
            )
            db.add(user)
            db.commit()
            db.refresh(user)

    # Auto promote phucsd@gmail.com if needed
    if user.email == "phucsd@gmail.com" and (not user.is_admin or not user.is_approved):
        user.is_admin = True
        user.is_approved = True
        user.is_verified = True
        db.commit()

    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản của bạn chưa được duyệt hoặc đã bị khóa bởi Admin. Vui lòng liên hệ Admin để được phê duyệt."
        )

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/oauth/login/{provider}")
def oauth_login(provider: str, redirect_uri: str, request: Request, state: str | None = None):
    """Initiates actual OAuth redirect (Google Authorize endpoint)."""
    _ = state
    if provider != "google":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hệ thống hiện tại chỉ hỗ trợ đăng nhập qua tài khoản Google."
        )

    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth chưa được cấu hình Client ID trên hệ thống."
        )

    safe_redirect = _validate_oauth_redirect_uri(redirect_uri, request)
    csrf_state = _sign_oauth_state(safe_redirect)
    query_str = urlencode(
        {
            "response_type": "code",
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": safe_redirect,
            "scope": "openid email profile",
            "state": csrf_state,
        }
    )
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{query_str}"
    return {"auth_url": auth_url, "state": csrf_state}

@router.get("/oauth/callback/{provider}", response_model=TokenResponse)
def oauth_callback(
    provider: str,
    code: str,
    redirect_uri: str,
    request: Request,
    state: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Processes callback and exchanges OAuth code for local JWT session token."""
    if provider != "google":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hệ thống hiện tại chỉ hỗ trợ đăng nhập qua tài khoản Google."
        )

    safe_redirect = _validate_oauth_redirect_uri(redirect_uri, request)
    _verify_oauth_state(state, safe_redirect)

    # 1. Exchange code for access token
    token_res = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": safe_redirect,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if token_res.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lỗi trao đổi mã xác thực OAuth với Google."
        )
    g_tokens = token_res.json()
    g_access_token = g_tokens.get("access_token")

    # 2. Get user info
    user_res = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {g_access_token}"},
        timeout=15,
    )
    if user_res.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lỗi lấy profile từ Google."
        )
    user_info = user_res.json()
    email = user_info.get("email")
    oauth_id = user_info.get("sub")
    if not user_info.get("email_verified"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email trên tài khoản Google chưa được xác minh."
        )
    username = user_info.get("name", (email or "user").split("@")[0]).replace(" ", "_")

    if not email or not oauth_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể xác định email hoặc OAuth ID từ tài khoản Google."
        )

    # Check requirement for admin approval setting
    from app.models import SystemSetting
    req_approval = db.query(SystemSetting).filter(SystemSetting.key == "require_admin_approval").first()
    system_requires_approval = (req_approval and req_approval.value.strip().lower() == "true")

    # 3. Resolve user in DB
    user = db.query(User).filter(
        User.oauth_provider == "google",
        User.oauth_id == oauth_id
    ).first()
    
    if not user:
        # Check if email is already registered under normal user
        user = db.query(User).filter(User.email == email).first()
        if user:
            # Associate
            user.oauth_provider = "google"
            user.oauth_id = oauth_id
            user.is_verified = True
            if email == "phucsd@gmail.com":
                user.is_admin = True
                user.is_approved = True
            db.commit()
        else:
            is_admin_user = (email == "phucsd@gmail.com")
            if not is_admin_user:
                allow_reg = db.query(SystemSetting).filter(SystemSetting.key == "allow_registration").first()
                is_reg_allowed = (
                    allow_reg.value.strip().lower() != "false"
                    if (allow_reg and allow_reg.value.strip())
                    else bool(settings.ALLOW_REGISTRATION)
                )
                if not is_reg_allowed:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Tính năng đăng ký tài khoản mới hiện đang bị tạm khóa bởi Quản trị viên.",
                    )
            # Create user
            user_id = generate_id("usr")
            rand_pwd = secrets.token_hex(16)
            hashed_pwd = get_password_hash(rand_pwd)
            api_key = f"ovg_live_{secrets.token_hex(24)}"
            
            # Ensure unique username
            base_username = username
            counter = 1
            while db.query(User).filter(User.username == username).first():
                username = f"{base_username}_{counter}"
                counter += 1
                
            is_approved = True if (is_admin_user or not system_requires_approval) else False

            user = User(
                id=user_id,
                username=username,
                email=email,
                hashed_password=hashed_pwd,
                is_verified=True,
                is_admin=is_admin_user,
                is_approved=is_approved,
                oauth_provider="google",
                oauth_id=oauth_id,
                api_key=api_key
            )
            db.add(user)
            db.commit()
            db.refresh(user)

    # Auto promote phucsd@gmail.com if needed
    if user.email == "phucsd@gmail.com" and (not user.is_admin or not user.is_approved):
        user.is_admin = True
        user.is_approved = True
        user.is_verified = True
        db.commit()

    if not user.is_approved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản của bạn chưa được duyệt hoặc đã bị khóa bởi Admin. Vui lòng liên hệ Admin để được phê duyệt."
        )

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}
