"""Authentication and authorization helpers for API middleware."""

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Set, Union


ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}


@dataclass(frozen=True)
class AuthFailure:
    status_code: int
    message: str


@dataclass(frozen=True)
class Principal:
    subject: str
    session_id: str
    workspace_id: str
    role: str
    scopes: Set[str]


class AuthSessionGuard:
    """Validates bearer credentials against current session rotation state."""

    def __init__(
        self,
        sessions: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ):
        self.sessions = dict(sessions or {})

    def authenticate(
        self,
        authorization: str,
        method: str,
        workspace_id: str,
    ) -> Union[Principal, AuthFailure]:
        parts = authorization.strip().split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return AuthFailure(401, "Unauthorized")

        token = parts[1].strip()
        if not token:
            return AuthFailure(401, "Unauthorized")

        claims = self._decode_token(token)
        if claims is None:
            return AuthFailure(401, "Malformed bearer token")

        session_id = self._string_claim(claims, "session_id")
        subject = self._string_claim(claims, "sub")
        if not session_id or not subject:
            return AuthFailure(401, "Malformed bearer token")

        session = self.sessions.get(session_id)
        if session is None:
            return AuthFailure(401, "Unknown session")

        if bool(claims.get("revoked")) or bool(session.get("revoked")):
            return AuthFailure(401, "Revoked session")

        token_rotation = claims.get("session_rotation")
        current_rotation = session.get("rotation")
        if token_rotation != current_rotation:
            return AuthFailure(401, "Stale session token")

        scopes = self._normalize_scopes(claims.get("scopes"))
        required_scope = self._required_scope(method)
        if required_scope not in scopes:
            return AuthFailure(
                403,
                f"Missing required scope: {required_scope}",
            )

        role = self._workspace_role(session, claims, workspace_id)
        required_role = (
            "operator" if self._is_write_method(method) else "viewer"
        )
        if ROLE_RANK.get(role, 0) < ROLE_RANK[required_role]:
            return AuthFailure(
                403,
                f"Insufficient workspace role: {required_role}",
            )

        return Principal(
            subject=subject,
            session_id=session_id,
            workspace_id=workspace_id,
            role=role,
            scopes=scopes,
        )

    @staticmethod
    def _decode_token(token: str) -> Optional[Dict[str, Any]]:
        padding = "=" * (-len(token) % 4)
        try:
            decoded = base64.urlsafe_b64decode(f"{token}{padding}").decode()
            claims = json.loads(decoded)
        except (binascii.Error, ValueError, json.JSONDecodeError):
            return None
        return claims if isinstance(claims, dict) else None

    @staticmethod
    def _string_claim(claims: Mapping[str, Any], key: str) -> str:
        value = claims.get(key)
        return value if isinstance(value, str) and value else ""

    @staticmethod
    def _normalize_scopes(value: Any) -> Set[str]:
        if isinstance(value, str):
            return {scope for scope in value.split() if scope}
        if isinstance(value, Iterable):
            return {
                scope for scope in value
                if isinstance(scope, str) and scope
            }
        return set()

    @staticmethod
    def _is_write_method(method: str) -> bool:
        return method.upper() not in {"GET", "HEAD", "OPTIONS"}

    def _required_scope(self, method: str) -> str:
        if self._is_write_method(method):
            return "orchestrator:write"
        return "orchestrator:read"

    @staticmethod
    def _workspace_role(
        session: Mapping[str, Any],
        claims: Mapping[str, Any],
        workspace_id: str,
    ) -> str:
        session_roles = session.get("roles")
        if isinstance(session_roles, Mapping):
            role = session_roles.get(workspace_id)
            if isinstance(role, str):
                return role

        claim_roles = claims.get("roles")
        if isinstance(claim_roles, Mapping):
            role = claim_roles.get(workspace_id)
            if isinstance(role, str):
                return role

        role = claims.get("workspace_role")
        if isinstance(role, str):
            return role
        return ""
