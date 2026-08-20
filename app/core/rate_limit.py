from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

# --- 1. SHARED STORAGE ---
# Redis instead of the default memory:// backend. In-memory counters live inside a
# single process, so N uvicorn workers each keep their own tally and a "5/minute"
# limit really means 5*N per minute. They are also lost on every restart, so a
# redeploy hands an attacker a fresh budget. Redis gives every worker one view.
#
# --- 2. SURVIVING A REDIS OUTAGE ---
# This is the one thing memory:// gave us for free: it cannot fail. Redis can, and
# the limiter guards /login, /register, /forgot-password and /resend-verification -
# so without a fallback the whole auth surface would go down with the cache.
#
# in_memory_fallback_enabled is what handles that, NOT swallow_errors. The obvious
# looking swallow_errors=True is a trap here: it suppresses the failed limit check
# but leaves request.state.view_rate_limit unset, and slowapi's own wrapper then
# reads that attribute unconditionally on the way out - so the request dies with an
# AttributeError instead of the error it just swallowed. Verified against slowapi
# 0.1.10, not assumed.
#
# The fallback degrades to per-process counting while Redis is away - exactly the
# behaviour we had before this change - and slowapi probes the backend periodically
# and switches back on its own once Redis answers again.
#
# --- 3. TELLING THE CLIENT WHEN TO COME BACK ---
# headers_enabled makes slowapi send Retry-After (plus the X-RateLimit-* trio) with
# the seconds left in the window. Without it a 429 body only says "Rate limit
# exceeded: 5 per 1 minute", which carries no deadline - the login page had to guess
# a flat 60 seconds. The header is an integer here, not an HTTP date, because that
# formatting only kicks in when retry_after is set to "http-date".
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
    in_memory_fallback_enabled=True,
    headers_enabled=True,
)
