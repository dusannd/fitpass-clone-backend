from pydantic import BaseModel

class QRTokenResponse(BaseModel):
    qr_token: str
    expires_in_seconds: int = 60

class ScanRequest(BaseModel):
    qr_token: str
    location_id: int


class ScanResponse(BaseModel):
    access_granted: bool
    message: str
    user_id: int