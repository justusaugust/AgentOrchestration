import hashlib
import json
import os

import pytest

import src.common.artifact_cache as artifact_cache
from src.common.artifact_cache import (
    ArtifactDigestMismatchError,
    ArtifactDownloadCache,
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class TestArtifactDownloadCache:
    def test_cache_hit_verifies_digest_before_reuse(self, tmp_path):
        payload = b"artifact-v1"
        calls = []
        cache = ArtifactDownloadCache(tmp_path)

        first_path = cache.get(
            "models/model.bin",
            expected_sha256=sha256(payload),
            fetcher=lambda: calls.append("download") or payload,
        )
        second_path = cache.get(
            "models/model.bin",
            expected_sha256=sha256(payload),
            fetcher=lambda: calls.append("unexpected") or b"bad",
        )

        assert first_path == second_path
        assert second_path.read_bytes() == payload
        assert calls == ["download"]

    def test_digest_mismatch_evicts_and_redownloads(self, tmp_path):
        payload = b"good-artifact"
        calls = []
        cache = ArtifactDownloadCache(tmp_path)

        cached_path = cache.get(
            "packages/tool.tar",
            expected_sha256=sha256(payload),
            fetcher=lambda: payload,
        )
        cached_path.write_bytes(b"bad-artifact!")

        fresh_path = cache.get(
            "packages/tool.tar",
            expected_sha256=sha256(payload),
            fetcher=lambda: calls.append("download") or payload,
        )

        assert fresh_path == cached_path
        assert fresh_path.read_bytes() == payload
        assert calls == ["download"]

    def test_partial_file_evicts_and_redownloads(self, tmp_path):
        payload = b"complete-artifact-payload"
        calls = []
        cache = ArtifactDownloadCache(tmp_path)

        cached_path = cache.get(
            "runs/output.json",
            expected_sha256=sha256(payload),
            fetcher=lambda: payload,
        )
        cached_path.write_bytes(payload[:7])

        fresh_path = cache.get(
            "runs/output.json",
            expected_sha256=sha256(payload),
            fetcher=lambda: calls.append("download") or payload,
        )

        assert fresh_path.read_bytes() == payload
        assert calls == ["download"]

    def test_metadata_records_expected_digest(self, tmp_path):
        payload = b"metadata-worthy-artifact"
        cache = ArtifactDownloadCache(tmp_path)

        artifact_path = cache.get(
            "images/base.img",
            expected_sha256=sha256(payload),
            fetcher=lambda: payload,
        )
        metadata = cache.read_metadata("images/base.img")

        assert artifact_path.exists()
        assert metadata["sha256"] == sha256(payload)
        assert metadata["size"] == len(payload)

    def test_missing_digest_metadata_evicts_and_redownloads(self, tmp_path):
        payload = b"artifact-with-metadata"
        calls = []
        cache = ArtifactDownloadCache(tmp_path)

        artifact_path = cache.get(
            "reports/result.json",
            expected_sha256=sha256(payload),
            fetcher=lambda: payload,
        )
        metadata_path = cache._metadata_path("reports/result.json")
        metadata_path.write_text(json.dumps({"key": "reports/result.json"}))

        fresh_path = cache.get(
            "reports/result.json",
            expected_sha256=sha256(payload),
            fetcher=lambda: calls.append("download") or payload,
        )

        assert fresh_path == artifact_path
        assert fresh_path.read_bytes() == payload
        metadata = cache.read_metadata("reports/result.json")
        assert metadata["sha256"] == sha256(payload)
        assert calls == ["download"]

    def test_download_digest_mismatch_rejects_bad_artifact(self, tmp_path):
        cache = ArtifactDownloadCache(tmp_path)

        with pytest.raises(ArtifactDigestMismatchError):
            cache.get(
                "downloads/archive.tgz",
                expected_sha256=sha256(b"expected-artifact"),
                fetcher=lambda: b"tampered-artifact",
            )

        with pytest.raises(FileNotFoundError):
            cache.read_metadata("downloads/archive.tgz")

    def test_bad_refresh_download_preserves_valid_artifact(self, tmp_path):
        old_payload = b"old-valid-artifact"
        new_payload = b"new-valid-artifact"
        cache = ArtifactDownloadCache(tmp_path)

        old_path = cache.get(
            "downloads/tool",
            expected_sha256=sha256(old_payload),
            fetcher=lambda: old_payload,
        )

        with pytest.raises(ArtifactDigestMismatchError):
            cache.get(
                "downloads/tool",
                expected_sha256=sha256(new_payload),
                fetcher=lambda: b"not-the-new-artifact",
            )

        assert old_path.read_bytes() == old_payload
        metadata = cache.read_metadata("downloads/tool")
        assert metadata["sha256"] == sha256(old_payload)

    def test_atomic_write_uses_unique_same_dir_temp(
        self,
        tmp_path,
        monkeypatch,
    ):
        target = tmp_path / "artifact.bin"
        replacements = []
        original_replace = os.replace

        def recording_replace(source, destination):
            replacements.append((source, destination))
            original_replace(source, destination)

        monkeypatch.setattr(artifact_cache.os, "replace", recording_replace)

        artifact_cache._atomic_write(target, b"payload")

        assert target.read_bytes() == b"payload"
        assert len(replacements) == 1
        source, destination = replacements[0]
        assert destination == target
        assert source.parent == target.parent
        assert source != target.with_name(f"{target.name}.tmp")
