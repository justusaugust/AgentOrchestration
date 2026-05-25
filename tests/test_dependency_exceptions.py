from datetime import date

import pytest
import yaml

from scripts.validate_dependency_exceptions import (
    ValidationError,
    enforce_dependency_review_overrides,
    load_dependency_review_changes,
    validate_manifest,
    write_dependency_review_config,
    write_markdown_summary,
    write_used_exception_summary,
)


def write_manifest(tmp_path, content):
    manifest = tmp_path / "dependency-exceptions.yml"
    manifest.write_text(content)
    return manifest


def valid_manifest(tmp_path):
    return write_manifest(
        tmp_path,
        """
exceptions:
  - dependency: pkg:pypi/example-vulnerable@1.2.3
    advisory: GHSA-abcd-1234-efgh
    owner: security@example.com
    reason: Waiting for upstream patched release.
    expiration: 2026-06-30
""",
    )


def test_valid_manifest_returns_active_exception(tmp_path):
    result = validate_manifest(
        valid_manifest(tmp_path),
        today=date(2026, 5, 25),
    )

    assert len(result.active_exceptions) == 1
    exception = result.active_exceptions[0]
    assert exception.dependency == "pkg:pypi/example-vulnerable@1.2.3"
    assert exception.advisory == "GHSA-abcd-1234-efgh"
    assert exception.owner == "security@example.com"
    assert exception.line == 3


def test_manifest_requires_all_audit_fields(tmp_path):
    manifest = write_manifest(
        tmp_path,
        """
exceptions:
  - dependency: pkg:pypi/example-vulnerable@1.2.3
    advisory: GHSA-abcd-1234-efgh
    owner: security@example.com
    expiration: 2026-06-30
""",
    )

    with pytest.raises(ValidationError, match="reason"):
        validate_manifest(manifest, today=date(2026, 5, 25))


def test_expired_exception_fails_validation(tmp_path):
    manifest = write_manifest(
        tmp_path,
        """
exceptions:
  - dependency: pkg:pypi/example-vulnerable@1.2.3
    advisory: GHSA-abcd-1234-efgh
    owner: security@example.com
    reason: Temporary exception expired yesterday.
    expiration: 2026-05-24
""",
    )

    with pytest.raises(ValidationError, match="expired"):
        validate_manifest(manifest, today=date(2026, 5, 25))


def test_duplicate_dependency_advisory_pair_fails_validation(tmp_path):
    manifest = write_manifest(
        tmp_path,
        """
exceptions:
  - dependency: pkg:pypi/example-vulnerable@1.2.3
    advisory: GHSA-abcd-1234-efgh
    owner: security@example.com
    reason: First exception record.
    expiration: 2026-06-30
  - dependency: pkg:pypi/example-vulnerable@1.2.3
    advisory: GHSA-abcd-1234-efgh
    owner: platform@example.com
    reason: Duplicate exception record.
    expiration: 2026-07-31
""",
    )

    with pytest.raises(ValidationError, match="duplicate"):
        validate_manifest(manifest, today=date(2026, 5, 25))


def test_generated_dependency_review_config_uses_active_advisories(tmp_path):
    result = validate_manifest(
        valid_manifest(tmp_path),
        today=date(2026, 5, 25),
    )
    config = tmp_path / "dependency-review-config.yml"

    write_dependency_review_config(result, config)

    assert yaml.safe_load(config.read_text()) == {
        "warn-only": True,
        "license-check": False,
        "vulnerability-check": True,
    }


def test_dependency_review_override_requires_exact_package_match(tmp_path):
    result = validate_manifest(
        valid_manifest(tmp_path),
        today=date(2026, 5, 25),
    )
    vulnerable_changes = [
        {
            "package_url": "pkg:pypi/different-package@1.2.3",
            "vulnerabilities": [
                {"advisory_ghsa_id": "GHSA-abcd-1234-efgh"},
            ],
        }
    ]

    with pytest.raises(ValidationError, match="without exact manifest"):
        enforce_dependency_review_overrides(result, vulnerable_changes)


def test_dependency_review_override_allows_exact_manifest_pair(tmp_path):
    result = validate_manifest(
        valid_manifest(tmp_path),
        today=date(2026, 5, 25),
    )
    vulnerable_changes = [
        {
            "package_url": "pkg:pypi/example-vulnerable@1.2.3",
            "vulnerabilities": [
                {"advisory_ghsa_id": "GHSA-abcd-1234-efgh"},
            ],
        }
    ]

    overrides = enforce_dependency_review_overrides(
        result,
        vulnerable_changes,
    )

    assert len(overrides) == 1
    assert overrides[0].dependency == "pkg:pypi/example-vulnerable@1.2.3"
    assert overrides[0].exception.line == 3


def test_markdown_summary_links_exceptions_to_manifest_records(tmp_path):
    manifest = valid_manifest(tmp_path)
    result = validate_manifest(manifest, today=date(2026, 5, 25))
    summary = tmp_path / "summary.md"

    write_markdown_summary(
        result,
        summary,
        repo_url="https://github.com/example/repo",
        ref="abc123",
    )

    markdown = summary.read_text()
    assert "pkg:pypi/example-vulnerable@1.2.3" in markdown
    assert "GHSA-abcd-1234-efgh" in markdown
    expected_link = (
        "https://github.com/example/repo/blob/abc123/"
        "dependency-exceptions.yml#L3"
    )
    assert expected_link in markdown


def test_used_override_summary_links_exact_exception_record(tmp_path):
    manifest = valid_manifest(tmp_path)
    result = validate_manifest(manifest, today=date(2026, 5, 25))
    overrides = enforce_dependency_review_overrides(
        result,
        [
            {
                "package_url": "pkg:pypi/example-vulnerable@1.2.3",
                "vulnerabilities": [
                    {"advisory_ghsa_id": "GHSA-abcd-1234-efgh"},
                ],
            }
        ],
    )
    summary = tmp_path / "used-summary.md"

    write_used_exception_summary(
        result,
        overrides,
        summary,
        repo_url="https://github.com/example/repo",
        ref="abc123",
    )

    markdown = summary.read_text()
    assert "Dependency Review Overrides Used" in markdown
    assert "pkg:pypi/example-vulnerable@1.2.3" in markdown
    assert "https://github.com/example/repo/blob/abc123/" in markdown


def test_load_empty_dependency_review_output(tmp_path):
    output = tmp_path / "vulnerable-changes.json"
    output.write_text("")

    assert load_dependency_review_changes(output) == []
