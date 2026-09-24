"""Explicit release retirement decisions with stable, replayable assessments."""

from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

if __package__:
    from . import declarations
else:
    import declarations


TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise ValueError("Retirement timestamps must use YYYY-MM-DDTHH:MM:SSZ")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate(data):
    if (
        not isinstance(data, dict)
        or set(data) != {"schemaVersion", "retirements"}
        or type(data["schemaVersion"]) is not int
        or data["schemaVersion"] != 1
        or not isinstance(data["retirements"], dict)
    ):
        raise ValueError("Unsupported policy support schema")
    for version, decision in data["retirements"].items():
        declarations.require_policy_version(version)
        if not isinstance(decision, dict) or set(decision) != {
            "decision",
            "reason",
            "migrationStartsAt",
            "retiresAt",
        }:
            raise ValueError(
                f"Retirement of {version} needs a decision, reason, and migration period"
            )
        if any(
            not isinstance(decision[key], str) or not decision[key].strip()
            for key in ("decision", "reason")
        ):
            raise ValueError(f"Retirement of {version} needs a decision and reason")
        reference = urlsplit(decision["decision"])
        if (
            reference.scheme != "https"
            or not reference.netloc
            or reference.username
            or reference.password
        ):
            raise ValueError("Retirement decisions need an HTTPS review reference")
        if timestamp(decision["migrationStartsAt"]) >= timestamp(decision["retiresAt"]):
            raise ValueError("Retirement needs an explicit positive migration period")
    return data


def assess(version, decisions, *, at=None):
    declarations.require_policy_version(version)
    decision = decisions["retirements"].get(version)
    retired = decision is not None and (at or now()) >= timestamp(decision["retiresAt"])
    # Time is an evaluation input, not evidence identity. Only the effective state changes.
    return {
        "policyVersion": version,
        "status": "retired" if retired else "supported",
        "retirement": decision,
    }


def retirement_issue(assessment):
    return f"Policy {assessment['policyVersion']} retired at {assessment['retirement']['retiresAt']}; its migration period has ended"
