#!/usr/bin/env python3
"""Validate tracked dependency-review exceptions and generate CI artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import yaml


REQUIRED_FIELDS = ("dependency", "advisory", "owner", "reason", "expiration")
ADVISORY_PATTERN = re.compile(
    r"^GHSA-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}$"
)


class ValidationError(Exception):
    """Raised when the exception manifest is malformed or unsafe."""


class LineLoader(yaml.SafeLoader):
    """YAML loader that preserves source line numbers on mappings."""


def _construct_mapping_with_line(
    loader: LineLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[str, Any]:
    mapping = yaml.SafeLoader.construct_mapping(loader, node, deep=deep)
    mapping["__line__"] = node.start_mark.line + 1
    return mapping


LineLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_with_line,
)


@dataclass(frozen=True)
class DependencyException:
    dependency: str
    advisory: str
    owner: str
    reason: str
    expiration: date
    line: int


@dataclass(frozen=True)
class ValidationResult:
    manifest_path: Path
    active_exceptions: tuple[DependencyException, ...]


@dataclass(frozen=True)
class DependencyReviewOverride:
    dependency: str
    advisory: str
    exception: DependencyException


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.load(manifest_path.read_text(), Loader=LineLoader)
    except yaml.YAMLError as exc:
        raise ValidationError(f"{manifest_path}: invalid YAML: {exc}") from exc

    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ValidationError(f"{manifest_path}: manifest must be a mapping")
    return loaded


def validate_manifest(
    manifest_path: Path | str,
    *,
    today: date | None = None,
) -> ValidationResult:
    path = Path(manifest_path)
    current_date = today or date.today()
    manifest = load_manifest(path)

    raw_exceptions = manifest.get("exceptions")
    if raw_exceptions is None:
        raise ValidationError(
            f"{path}: missing required top-level 'exceptions' list"
        )
    if not isinstance(raw_exceptions, list):
        raise ValidationError(f"{path}: 'exceptions' must be a list")

    active: list[DependencyException] = []
    seen: dict[tuple[str, str], int] = {}

    for index, raw_exception in enumerate(raw_exceptions, start=1):
        if not isinstance(raw_exception, dict):
            raise ValidationError(
                f"{path}: exception #{index} must be a mapping"
            )

        line = int(raw_exception.get("__line__", index))
        missing = [
            field
            for field in REQUIRED_FIELDS
            if field not in raw_exception or _is_blank(raw_exception[field])
        ]
        if missing:
            raise ValidationError(
                f"{path}:{line}: missing required field(s): "
                f"{', '.join(missing)}"
            )

        dependency = _string_field(raw_exception, "dependency", path, line)
        advisory = _string_field(raw_exception, "advisory", path, line)
        owner = _string_field(raw_exception, "owner", path, line)
        reason = _string_field(raw_exception, "reason", path, line)
        expiration = _expiration_field(raw_exception["expiration"], path, line)

        if not dependency.startswith("pkg:"):
            raise ValidationError(
                f"{path}:{line}: dependency must be a package URL "
                "beginning with 'pkg:'"
            )
        if not ADVISORY_PATTERN.match(advisory):
            raise ValidationError(
                f"{path}:{line}: advisory must be a GitHub advisory ID like "
                "GHSA-abcd-1234-efgh"
            )

        key = (dependency, advisory)
        if key in seen:
            raise ValidationError(
                f"{path}:{line}: duplicate exception for "
                f"{dependency} / {advisory}; "
                f"first declared on line {seen[key]}"
            )
        seen[key] = line

        if expiration < current_date:
            raise ValidationError(
                f"{path}:{line}: exception for "
                f"{dependency} / {advisory} expired "
                f"on {expiration.isoformat()}"
            )

        active.append(
            DependencyException(
                dependency=dependency,
                advisory=advisory,
                owner=owner,
                reason=reason,
                expiration=expiration,
                line=line,
            )
        )

    return ValidationResult(
        manifest_path=path,
        active_exceptions=tuple(active),
    )


def write_dependency_review_config(
    result: ValidationResult,
    output_path: Path | str,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "warn-only": True,
                "license-check": False,
                "vulnerability-check": True,
            },
            sort_keys=False,
        )
    )


def write_markdown_summary(
    result: ValidationResult,
    output_path: Path | str,
    *,
    repo_url: str | None = None,
    ref: str | None = None,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_markdown_summary(result, repo_url=repo_url, ref=ref)
    )


def load_dependency_review_changes(changes_path: Path | str) -> list[Any]:
    path = Path(changes_path)
    raw_json = path.read_text().strip()
    if not raw_json:
        return []

    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"{path}: dependency-review output is not valid JSON: {exc}"
        ) from exc

    if parsed is None:
        return []
    if not isinstance(parsed, list):
        raise ValidationError(
            f"{path}: dependency-review output must be a JSON list"
        )
    return parsed


def enforce_dependency_review_overrides(
    result: ValidationResult,
    vulnerable_changes: list[Any],
) -> tuple[DependencyReviewOverride, ...]:
    exceptions_by_key = {
        (exception.dependency, exception.advisory): exception
        for exception in result.active_exceptions
    }
    used: list[DependencyReviewOverride] = []
    unmatched: list[str] = []

    for change_index, raw_change in enumerate(vulnerable_changes, start=1):
        if not isinstance(raw_change, dict):
            raise ValidationError(
                "dependency-review vulnerable change "
                f"#{change_index} must be a mapping"
            )

        package_url = raw_change.get("package_url")
        if not isinstance(package_url, str) or not package_url.strip():
            raise ValidationError(
                "dependency-review vulnerable change "
                f"#{change_index} is missing package_url"
            )

        vulnerabilities = raw_change.get("vulnerabilities") or []
        if not isinstance(vulnerabilities, list):
            raise ValidationError(
                f"{package_url}: vulnerabilities must be a list"
            )

        for vulnerability_index, vulnerability in enumerate(
            vulnerabilities,
            start=1,
        ):
            if not isinstance(vulnerability, dict):
                raise ValidationError(
                    f"{package_url}: vulnerability #{vulnerability_index} "
                    "must be a mapping"
                )

            advisory = vulnerability.get("advisory_ghsa_id")
            if not isinstance(advisory, str) or not advisory.strip():
                raise ValidationError(
                    f"{package_url}: vulnerability #{vulnerability_index} "
                    "is missing advisory_ghsa_id"
                )

            key = (package_url, advisory)
            exception = exceptions_by_key.get(key)
            if exception:
                used.append(
                    DependencyReviewOverride(
                        dependency=package_url,
                        advisory=advisory,
                        exception=exception,
                    )
                )
            else:
                unmatched.append(f"{package_url} / {advisory}")

    if unmatched:
        raise ValidationError(
            "dependency-review found vulnerable dependencies without exact "
            "manifest exceptions: " + ", ".join(unmatched)
        )

    return tuple(used)


def write_used_exception_summary(
    result: ValidationResult,
    overrides: tuple[DependencyReviewOverride, ...],
    output_path: Path | str,
    *,
    repo_url: str | None = None,
    ref: str | None = None,
) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_used_exception_summary(
            result,
            overrides,
            repo_url=repo_url,
            ref=ref,
        )
    )


def render_used_exception_summary(
    result: ValidationResult,
    overrides: tuple[DependencyReviewOverride, ...],
    *,
    repo_url: str | None = None,
    ref: str | None = None,
) -> str:
    lines = ["## Dependency Review Overrides Used", ""]
    if not overrides:
        lines.append("No dependency-review exceptions were used.")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| Dependency | Advisory | Manifest record |",
            "| --- | --- | --- |",
        ]
    )
    seen: set[tuple[str, str]] = set()
    for override in overrides:
        key = (override.dependency, override.advisory)
        if key in seen:
            continue
        seen.add(key)
        record_link = _manifest_link(
            result.manifest_path,
            override.exception.line,
            repo_url,
            ref,
        )
        lines.append(
            "| {dependency} | {advisory} | [line {line}]({record_link}) |"
            .format(
                dependency=_escape_markdown_cell(override.dependency),
                advisory=_escape_markdown_cell(override.advisory),
                line=override.exception.line,
                record_link=record_link,
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_markdown_summary(
    result: ValidationResult,
    *,
    repo_url: str | None = None,
    ref: str | None = None,
) -> str:
    lines = ["## Dependency Review Exceptions", ""]
    if not result.active_exceptions:
        lines.append("No active dependency-review exceptions are tracked.")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| Dependency | Advisory | Owner | Expiration | Manifest record |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for exception in result.active_exceptions:
        record_link = _manifest_link(
            result.manifest_path,
            exception.line,
            repo_url,
            ref,
        )
        lines.append(
            "| {dependency} | {advisory} | {owner} | {expiration} | "
            "[line {line}]({record_link}) |".format(
                dependency=_escape_markdown_cell(exception.dependency),
                advisory=_escape_markdown_cell(exception.advisory),
                owner=_escape_markdown_cell(exception.owner),
                expiration=exception.expiration.isoformat(),
                line=exception.line,
                record_link=record_link,
            )
        )
    lines.append("")
    return "\n".join(lines)


def _manifest_link(
    manifest_path: Path, line: int, repo_url: str | None, ref: str | None
) -> str:
    manifest_reference = manifest_path.as_posix()
    if manifest_path.is_absolute():
        manifest_reference = manifest_path.name
    if repo_url and ref:
        return (
            f"{repo_url.rstrip('/')}/blob/{ref}/"
            f"{manifest_reference}#L{line}"
        )
    return f"{manifest_reference}#L{line}"


def _string_field(
    raw_exception: dict[str, Any],
    field: str,
    manifest_path: Path,
    line: int,
) -> str:
    value = raw_exception[field]
    if not isinstance(value, str):
        raise ValidationError(
            f"{manifest_path}:{line}: {field} must be a string"
        )
    return value.strip()


def _expiration_field(value: Any, manifest_path: Path, line: int) -> date:
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError(
                f"{manifest_path}:{line}: expiration must use YYYY-MM-DD"
            ) from exc
    else:
        raise ValidationError(
            f"{manifest_path}:{line}: expiration must use YYYY-MM-DD"
        )

    return parsed


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _escape_markdown_cell(value: str) -> str:
    return value.replace("|", "\\|")


def _repository_url() -> str | None:
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        return None
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{server_url.rstrip('/')}/{repository}"


def _git_ref() -> str | None:
    return os.environ.get("GITHUB_SHA") or os.environ.get("GITHUB_REF_NAME")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate dependency exception manifest and write CI artifacts."
        )
    )
    parser.add_argument(
        "--manifest",
        default=".github/dependency-exceptions.yml",
        type=Path,
        help="Path to the tracked dependency exception manifest.",
    )
    parser.add_argument(
        "--dependency-review-config",
        type=Path,
        help=(
            "Optional path for the generated dependency-review-action config."
        ),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        help="Optional markdown summary output path.",
    )
    parser.add_argument(
        "--vulnerable-changes",
        type=Path,
        help=(
            "Optional JSON output from dependency-review-action "
            "'vulnerable-changes' to enforce against the manifest."
        ),
    )
    parser.add_argument(
        "--used-summary",
        type=Path,
        help=(
            "Optional markdown output path for exact dependency-review "
            "overrides used in the current PR."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        result = validate_manifest(args.manifest)
        if args.dependency_review_config:
            write_dependency_review_config(
                result,
                args.dependency_review_config,
            )
        if args.summary:
            write_markdown_summary(
                result,
                args.summary,
                repo_url=_repository_url(),
                ref=_git_ref(),
            )
        if args.vulnerable_changes:
            vulnerable_changes = load_dependency_review_changes(
                args.vulnerable_changes
            )
            overrides = enforce_dependency_review_overrides(
                result,
                vulnerable_changes,
            )
            if args.used_summary:
                write_used_exception_summary(
                    result,
                    overrides,
                    args.used_summary,
                    repo_url=_repository_url(),
                    ref=_git_ref(),
                )
    except ValidationError as exc:
        print(
            f"dependency exception validation failed: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        "validated {count} active dependency exception(s) from {path}".format(
            count=len(result.active_exceptions),
            path=args.manifest,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
