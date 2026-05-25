from scripts.audit_image_metadata import audit_metadata


def test_audit_accepts_approved_runtime_labels():
    inspect_payload = [
        {
            "Config": {
                "Labels": {
                    "org.opencontainers.image.title": "agent-orchestrator",
                    "org.opencontainers.image.description": "runtime",
                    "org.opencontainers.image.version": "ci",
                    "org.opencontainers.image.source": (
                        "https://github.com/example/repo"
                    ),
                },
                "Env": ["PYTHONUNBUFFERED=1"],
            }
        }
    ]

    violations = audit_metadata(
        "COPY src ./src",
        inspect_payload,
        ["secret-value"],
    )

    assert violations == []


def test_audit_rejects_forbidden_value_in_history():
    inspect_payload = [{"Config": {"Labels": {}}}]

    violations = audit_metadata(
        "RUN echo internal-build-value",
        inspect_payload,
        ["internal-build-value"],
    )

    assert violations == ["forbidden build value found in image history"]


def test_audit_rejects_forbidden_value_in_config():
    inspect_payload = [
        {
            "Config": {
                "Labels": {
                    "org.opencontainers.image.title": "internal-build-value",
                }
            }
        }
    ]

    violations = audit_metadata("", inspect_payload, ["internal-build-value"])

    assert violations == ["forbidden build value found in image config"]


def test_audit_rejects_unapproved_runtime_label():
    inspect_payload = [
        {
            "Config": {
                "Labels": {
                    "org.opencontainers.image.title": "agent-orchestrator",
                    "internal.build.config": "debug",
                }
            }
        }
    ]

    violations = audit_metadata("", inspect_payload, [])

    assert violations == ["unapproved runtime label: internal.build.config"]
