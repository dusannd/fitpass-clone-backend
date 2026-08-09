from fastapi import Depends, HTTPException, Request
import jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User


# --- NEW: FUNCTION TO EXTRACT COOKIE ---
def get_token_from_cookie(request: Request) -> str:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Missing cookie."
        )
    return token


def get_current_user_id(token: str = Depends(get_token_from_cookie)) -> int:
    """
    Extracts the token from the httpOnly cookie, decodes it, and returns the user ID.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")

        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token structure")

        return int(user_id)

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")


class RequireRole:
    """
    Dynamic dependency class for Role-Based Access Control (RBAC).
    """

    def __init__(self, required_role: str):
        self.required_role = required_role

    async def __call__(
            self,
            token: str = Depends(get_token_from_cookie),
            db: AsyncSession = Depends(get_db)
    ) -> int:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            user_id = payload.get("sub")
            token_roles = payload.get("roles", [])

            if user_id is None:
                raise HTTPException(status_code=401, detail="Invalid token structure")

            if self.required_role not in token_roles:
                raise HTTPException(
                    status_code=403,
                    detail=f"Forbidden: You don't have the '{self.required_role}' privilege"
                )

            # Use the INJECTED session instead of opening our own.
            # Calling AsyncSessionLocal() by hand ignores the get_db override,
            # so during tests every role protected route would still talk to
            # the real database instead of the test one.
            result = await db.execute(
                select(User).options(selectinload(User.roles)).where(User.id == int(user_id))
            )
            user = result.scalars().first()

            if not user:
                raise HTTPException(status_code=401, detail="User no longer exists.")

            actual_roles = [role.name for role in user.roles]

            if self.required_role not in actual_roles:
                raise HTTPException(
                    status_code=403,
                    detail="Your privileges have been revoked by an administrator."
                )

            return int(user_id)

        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired.")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token.")


get_current_admin = RequireRole("admin")