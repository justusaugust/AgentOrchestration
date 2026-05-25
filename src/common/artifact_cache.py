"""Local artifact download cache with checksum validation."""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Union


BytesLike = Union[bytes, bytearray, memoryview]


class ArtifactCacheError(Exception):
    """Base error for artifact cache failures."""


class ArtifactDigestMismatchError(ArtifactCacheError):
    """Raised when downloaded bytes fail digest validation."""


@dataclass(frozen=True)
class CacheResolution:
    """Resolved cache entry information for artifact consumers."""

    path: Path
    key_hash: str
    sha256: str
    size: int
    hit: bool


class ArtifactDownloadCache:
    """Caches downloaded artifacts and validates hits before reuse."""

    def __init__(self, cache_dir: Union[str, Path]):
        self.cache_dir = Path(cache_dir)
        self._objects_dir = self.cache_dir / "objects"
        self._metadata_dir = self.cache_dir / "metadata"
        self._objects_dir.mkdir(parents=True, exist_ok=True)
        self._metadata_dir.mkdir(parents=True, exist_ok=True)

    def get(
        self,
        key: str,
        *,
        expected_sha256: str,
        fetcher: Callable[[], BytesLike],
    ) -> Path:
        """Return a validated cached artifact, downloading it on cache miss."""
        return self.get_with_info(
            key,
            expected_sha256=expected_sha256,
            fetcher=fetcher,
        ).path

    def get_with_info(
        self,
        key: str,
        *,
        expected_sha256: str,
        fetcher: Callable[[], BytesLike],
    ) -> CacheResolution:
        """Return validated cached artifact details, downloading on misses."""
        expected_digest = _normalize_sha256(expected_sha256)
        key_hash = _cache_key_digest(key)
        metadata = self._load_metadata(key)
        existing_is_valid = (
            self._is_valid_for_metadata(key, metadata)
            if metadata
            else False
        )

        if (
            metadata
            and existing_is_valid
            and metadata.get("sha256") == expected_digest
        ):
            return CacheResolution(
                path=self._artifact_path(key, expected_digest),
                key_hash=key_hash,
                sha256=expected_digest,
                size=metadata["size"],
                hit=True,
            )

        if not existing_is_valid:
            self.evict(key)

        payload = fetcher()
        payload_bytes = _coerce_bytes(payload)
        actual_digest = hashlib.sha256(payload_bytes).hexdigest()
        if actual_digest != expected_digest:
            if not existing_is_valid:
                self.evict(key)
            raise ArtifactDigestMismatchError(
                "downloaded artifact sha256 mismatch for cache key hash "
                f"{key_hash}: expected {expected_digest}, got {actual_digest}"
            )

        artifact_path = self._artifact_path(key, expected_digest)
        _atomic_write(artifact_path, payload_bytes)
        self._write_metadata(
            key,
            {
                "key_hash": key_hash,
                "sha256": expected_digest,
                "size": len(payload_bytes),
            },
        )
        return CacheResolution(
            path=artifact_path,
            key_hash=key_hash,
            sha256=expected_digest,
            size=len(payload_bytes),
            hit=False,
        )

    def evict(self, key: str) -> None:
        """Remove a cached artifact and its metadata if either exists."""
        metadata = self._load_metadata(key)
        paths = [self._metadata_path(key)]
        if metadata:
            try:
                recorded_digest = _normalize_sha256(
                    str(metadata.get("sha256", ""))
                )
            except ValueError:
                recorded_digest = None
            if recorded_digest:
                paths.append(self._artifact_path(key, recorded_digest))

        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def read_metadata(self, key: str) -> Dict[str, object]:
        """Read cache metadata for inspection and tests."""
        metadata = self._load_metadata(key)
        if metadata is None:
            raise FileNotFoundError(self._metadata_path(key))
        return dict(metadata)

    def _is_valid_hit(
        self,
        key: str,
        expected_digest: str,
        metadata: Dict[str, object],
    ) -> bool:
        return (
            metadata.get("sha256") == expected_digest
            and self._is_valid_for_metadata(key, metadata)
        )

    def _is_valid_for_metadata(
        self,
        key: str,
        metadata: Dict[str, object],
    ) -> bool:
        expected_key_hash = _cache_key_digest(key)
        metadata_key_hash = metadata.get("key_hash")
        if metadata_key_hash is not None:
            if metadata_key_hash != expected_key_hash:
                return False
        elif metadata.get("key") != key:
            return False
        try:
            recorded_digest = _normalize_sha256(
                str(metadata.get("sha256", ""))
            )
        except ValueError:
            return False
        expected_size = metadata.get("size")
        if not isinstance(expected_size, int) or expected_size < 0:
            return False

        artifact_path = self._artifact_path(key, recorded_digest)
        if not artifact_path.is_file():
            return False
        if artifact_path.stat().st_size != expected_size:
            return False

        return _sha256_file(artifact_path) == recorded_digest

    def _load_metadata(self, key: str) -> Optional[Dict[str, object]]:
        try:
            with self._metadata_path(key).open() as metadata_file:
                metadata = json.load(metadata_file)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        if isinstance(metadata, dict):
            return metadata
        return None

    def _write_metadata(self, key: str, metadata: Dict[str, object]) -> None:
        payload = json.dumps(metadata, sort_keys=True).encode("utf-8")
        _atomic_write(self._metadata_path(key), payload)

    def _artifact_path(self, key: str, digest: str) -> Path:
        filename = f"{_cache_key_digest(key)}-{digest}.artifact"
        return self._objects_dir / filename

    def _metadata_path(self, key: str) -> Path:
        return self._metadata_dir / f"{_cache_key_digest(key)}.json"


def _cache_key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _normalize_sha256(digest: str) -> str:
    normalized = digest.lower()
    if normalized.startswith("sha256:"):
        normalized = normalized[len("sha256:"):]
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef"
        for character in normalized
    ):
        raise ValueError("expected_sha256 must be a SHA-256 hex digest")
    return normalized


def _coerce_bytes(payload: BytesLike) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, (bytearray, memoryview)):
        return bytes(payload)
    raise TypeError("artifact fetcher must return bytes-like content")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact_file:
        for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        "wb",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_file.name)
    try:
        with temp_file:
            temp_file.write(data)
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
