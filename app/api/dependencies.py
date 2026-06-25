from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# This tells FastAPI to look for the "Authorization: Bearer <token>" header
security = HTTPBearer()


def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
    """
    Extracts the token from the request header, decodes it, and returns the user ID.
    If the token is invalid or expired, it throws a 401 Unauthorized error.
    """
    token = credentials.credentials
    try:
        # Decode the token using our secret key
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
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

    def __call__(self, credentials: HTTPAuthorizationCredentials = Depends(security)) -> int:
        token = credentials.credentials
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub")

            # We now fetch the LIST of roles from the token
            user_roles = payload.get("roles", [])

            if user_id is None:
                raise HTTPException(status_code=401, detail="Invalid token structure")

            # Check if the user has the required role in their list of roles
            if self.required_role not in user_roles:
                raise HTTPException(
                    status_code=403,
                    detail=f"Forbidden: You don't have the '{self.required_role}' privilege"
                )

            return int(user_id)

        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired.")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token.")


# For backward compatibility with our current admin routes
# This automatically creates a dependency that checks for the "admin" role
get_current_admin = RequireRole("admin")