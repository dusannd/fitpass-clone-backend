import httpx
from fastapi import HTTPException, status
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)


async def verify_recaptcha(token: str | None) -> bool:
    """
    Verifies the reCAPTCHA token with Google's API.
    If FEATURE_RECAPTCHA is False, it immediately bypasses the check.
    """
    # 1. Check the Feature Flag (Issue 3 requirement)
    if not settings.FEATURE_RECAPTCHA:
        return True  # Bypass reCAPTCHA completely if feature is toggled off

    # 2. If the feature is ON, but the frontend forgot to send the token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="reCAPTCHA verification failed: Token is missing."
        )

    # 3. Prepare the request to Google
    verify_url = "https://www.google.com/recaptcha/api/siteverify"
    payload = {
        "secret": settings.RECAPTCHA_SECRET,
        "response": token
    }

    try:
        # We use httpx.AsyncClient so we don't block the FastAPI event loop
        async with httpx.AsyncClient() as client:
            response = await client.post(verify_url, data=payload)
            result = response.json()

            # 4. Check Google's response
            if result.get("success"):
                return True
            else:
                logger.warning(f"reCAPTCHA validation failed. Errors: {result.get('error-codes')}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="reCAPTCHA verification failed. Are you a bot?"
                )

    except httpx.RequestError as e:
        logger.error(f"Error communicating with Google reCAPTCHA API: {e}")
        # If Google's API is down, fail safely
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Security verification service is temporarily unavailable."
        )