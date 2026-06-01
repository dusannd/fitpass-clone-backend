from pydantic import BaseModel
from datetime import datetime

class QRTokenResponse(BaseModel):
    qr_token: str
    expires_in_seconds: int = 60

class ScanRequest(BaseModel):
    qr_token: str

class ScanResponse(BaseModel):
    access_granted: bool
    message: str
    user_id: int