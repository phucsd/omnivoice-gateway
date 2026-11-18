import hmac
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings

security_scheme = HTTPBearer(auto_error=False)


def verify_worker_token(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
):
    """Verifies that the provided Bearer token matches the configured system WORKER_TOKEN."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing worker authentication token.",
        )

    token = credentials.credentials.strip()
    expected = (settings.WORKER_TOKEN or "").strip()

    if expected and hmac.compare_digest(token, expected):
        return token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid worker token.",
    )
