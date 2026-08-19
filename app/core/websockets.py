from fastapi import WebSocket
from typing import Dict
import logging

logger = logging.getLogger(__name__)

class ConnectionManager:
    """
    Manages active WebSocket connections for real-time gym access feedback.
    """
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}

    def connect(self, websocket: WebSocket, user_id: int):
        """
        Registers an ALREADY-ACCEPTED socket.

        The handshake is deliberately accepted by the endpoint before this call,
        not here. Closing a socket that was never accepted makes Starlette answer
        the upgrade request with a plain HTTP 403, and the WebSocket spec forbids
        browsers from exposing that status to JavaScript - the client would only
        see close code 1006 and could not tell an expired session apart from a
        dropped network.
        """
        self.active_connections[user_id] = websocket
        logger.info(f"User {user_id} connected to WebSocket.")

    def disconnect(self, user_id: int, websocket: WebSocket | None = None):
        """
        Removes a user's socket, but only if the map still points at `websocket`.

        This guard matters now that the frontend reconnects on its own. A phone
        that switches networks opens a fresh socket while the old one may not have
        noticed the drop yet; when that stale handler finally unwinds, deleting
        the entry blindly would unregister the LIVE connection and silently stop
        the member's turnstile events. Pass the socket to make the removal
        identity-checked. Called without one it stays unconditional, as before.
        """
        current = self.active_connections.get(user_id)
        if current is None:
            return

        if websocket is not None and current is not websocket:
            logger.info(f"Skipped disconnect for user {user_id}: a newer socket is registered.")
            return

        del self.active_connections[user_id]
        logger.info(f"User {user_id} disconnected from WebSocket.")

    async def send_personal_message(self, message: dict, user_id: int):
        websocket = self.active_connections.get(user_id)
        if websocket:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"Failed to send WS message to user {user_id}: {e}")
                self.disconnect(user_id)

# Global instance
ws_manager = ConnectionManager()