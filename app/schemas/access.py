from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

class QRTokenResponse(BaseModel):
    qr_token: str
    expires_in_seconds: int = 60

# --- NEW: Schema for generating QR ---
class GenerateQRRequest(BaseModel):
    action_type: str  # Expected: "ENTRY" or "EXIT"

class ScanRequest(BaseModel):
    qr_token: str
    location_id: int
    scan_type: str #Expected "ENTRY" or "EXIT"


class ScanResponse(BaseModel):
    access_granted: bool
    message: str
    user_id: int
    action_type: Optional[str] = None #Tells the scanner what action was performed


# --- ADMIN ANALYTICS SCHEMAS ---

class BasicUser(BaseModel):
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    class Config:
        from_attributes = True


class BasicLocation(BaseModel):
    name: str

    class Config:
        from_attributes = True


class AdminEntryLogResponse(BaseModel):
    id: int
    timestamp: datetime
    access_granted: bool
    reason: Optional[str]
    action_type: str #Admin audit logs

    # Nested relationships (FastAPI will automatically extract these from the DB models!)
    location: Optional[BasicLocation] = None
    worker: Optional[BasicUser] = None
    user: Optional[BasicUser] = None  # We need this to see who entered during the audit

model_config = ConfigDict(from_attributes=True)