from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from sqlalchemy.future import select

# NOVO: Svi uvozi su sada čisti i koriste Settings
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.user import User

# This tells FastAPI to look for the "Authorization: Bearer <token>" header
security = HTTPBearer()


def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
    """
    Extracts the token from the request header, decodes it, and returns the user ID.
    If the token is invalid or expired, it throws a 401 Unauthorized error.
    """
    token = credentials.credentials
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
    A dynamic dependency class for Role-Based Access Control (RBAC).
    Usage in endpoints: Depends(RequireRole("admin")) or Depends(RequireRole("worker"))
    """

    def __init__(self, required_role: str):
        self.required_role = required_role

    async def __call__(self, credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
        token = credentials.credentials
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            user_id = payload.get("sub")

            # This is what the token CLAIMS the user has
            token_roles = payload.get("roles", [])

            if user_id is None:
                raise HTTPException(status_code=401, detail="Invalid token structure")

            # 1. Check if the token claims they have the role
            if self.required_role not in token_roles:
                raise HTTPException(
                    status_code=403,
                    detail=f"Forbidden: You don't have the '{self.required_role}' privilege"
                )

            # 2. ANTI-ZOMBIE TOKEN FIX: Verify against the actual database!
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(User).where(User.id == int(user_id)))
                user = result.scalars().first()

                if not user:
                    raise HTTPException(status_code=401, detail="User no longer exists.")

                # Fetch actual roles from the database via lazy loading
                actual_roles = [role.name for role in await user.awaitable_attrs.roles]

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


# For backward compatibility with our current admin routes
get_current_admin = RequireRole("admin")