"""Audit final Docker image metadata for build-only value leaks."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Iterable


APPROVED_RUNTIME_LABELS = {
    "org.opencontainers.image.title",
    "org.opencontainers.image.description",
    "org.opencontainers.image.version",
    "org.opencontainers.image.source",
}


def audit_metadata(
    history_text: str,
    inspect_payload: list[dict],
    forbidden_values: Iterable[str],
) -> list[str]:
    """Return audit violations found in final image history or config."""

    violations: list[str] = []
    config = (inspect_payload[0] if inspect_payload else {}).get("Config", {})
    labels = config.get("Labels") or {}
    metadata_text = json.dumps(config, sort_keys=True)

    unknown_labels = sorted(set(labels) - APPROVED_RUNTIME_LABELS)
    for label in unknown_labels:
        violations.append(f"unapproved runtime label: {label}")

    for value in forbidden_values:
        if not value:
            continue
        if value in history_text:
            violations.append("forbidden build value found in image history")
        if value in metadata_text:
            violations.append("forbidden build value found in image config")

    return violations


def _docker_json(args: list[str]) -> list[dict]:
    output = subprocess.check_output(args, text=True)
    return json.loads(output)


def _docker_text(args: list[str]) -> str:
    return subprocess.check_output(args, text=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", help="Docker image reference to audit")
    parser.add_argument(
        "--forbidden",
        action="append",
        default=[],
        help="Build-only value that must not appear in final image metadata",
    )
    args = parser.parse_args(argv)

    history_text = _docker_text(
        [
            "docker",
            "history",
            "--no-trunc",
            "--format",
            "{{.CreatedBy}}",
            args.image,
        ]
    )
    inspect_payload = _docker_json(["docker", "image", "inspect", args.image])
    violations = audit_metadata(history_text, inspect_payload, args.forbidden)
    if violations:
        for violation in violations:
            print(f"image metadata audit failed: {violation}", file=sys.stderr)
        return 1

    print("image metadata audit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
