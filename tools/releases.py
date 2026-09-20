"""Resolve published immutable checkers and validate their member reports."""

import json
import os
import re
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

if __package__:
    from . import declarations, records
else:
    import declarations
    import records


REVISION = re.compile(r"[0-9a-f]{40}\Z")


class InvalidRelease(ValueError):
    """Available metadata proves the selected release is not an allowed release."""


def version_at_least(version, minimum):
    declarations.require_policy_version(version)
    return tuple(map(int, version[1:].split("."))) >= minimum


def public_get(path):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(
            Request(f"https://api.github.com/{path}", headers=headers), timeout=30
        ) as response:
            return json.load(response, object_pairs_hook=records.unique_mapping)
    except HTTPError as error:
        raise ValueError(
            f"Policy release inspection unavailable: HTTP {error.code}"
        ) from error


def inspect_release(repository, version):
    declarations.require_policy_version(version)
    if not isinstance(repository, str) or not records.REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid trusted policy repository")
    release = public_get(f"repos/{repository}/releases/tags/{version}")
    if (
        isinstance(release, dict)
        and release.get("tag_name") == version
        and (
            release.get("immutable") is False
            or release.get("draft") is True
            or release.get("prerelease") is True
        )
    ):
        raise InvalidRelease(
            "Selected policy must be a published immutable non-prerelease"
        )
    if (
        not isinstance(release, dict)
        or release.get("tag_name") != version
        or release.get("immutable") is not True
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise ValueError("Selected policy must be a published immutable non-prerelease")
    reference = public_get(f"repos/{repository}/git/ref/tags/{version}")
    seen = set()
    for _ in range(8):
        target = reference.get("object") if isinstance(reference, dict) else None
        if (
            not isinstance(target, dict)
            or not isinstance(target.get("sha"), str)
            or not REVISION.fullmatch(target["sha"])
            or target["sha"] in seen
        ):
            raise ValueError("Policy release tag has no inspectable exact commit")
        if target.get("type") == "commit":
            return {
                "repository": repository,
                "version": version,
                "revision": target["sha"],
            }
        if target.get("type") != "tag":
            break
        seen.add(target["sha"])
        reference = public_get(f"repos/{repository}/git/tags/{target['sha']}")
    raise ValueError("Policy release tag does not resolve to a commit")


def checker_command(release, records_root, *arguments):
    return [
        "nix",
        "run",
        "--no-update-lock-file",
        f"github:{release['repository']}/{release['revision']}",
        "--",
        "--policy-root",
        str(records_root.resolve()),
        *map(str, arguments),
    ]


def published_versions(repository):
    versions = set()
    for page in range(1, 101):
        data = public_get(f"repos/{repository}/releases?per_page=100&page={page}")
        if not isinstance(data, list):
            raise ValueError("Published policy release inventory is unavailable")
        for release in data:
            if not isinstance(release, dict):
                raise ValueError("Published policy release inventory is malformed")
            if release.get("draft") is True or release.get("prerelease") is True:
                continue
            if (
                release.get("draft") is not False
                or release.get("prerelease") is not False
                or release.get("immutable") is not True
            ):
                raise ValueError(
                    "Published policy release inventory is incomplete or mutable"
                )
            declarations.require_policy_version(release.get("tag_name"))
            versions.add(release["tag_name"])
        if len(data) < 100:
            return sorted(versions)
    raise ValueError("Published policy release inventory could not be completed")


def check_member(
    release,
    root,
    name,
    repository,
    revision,
    records_root,
    snapshot,
    *,
    settings=None,
    support_assessment,
):
    process = subprocess.run(
        checker_command(
            release, records_root, "check", root.resolve(), "--project", name
        ),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if process.returncode not in {0, 1}:
        raise ValueError(process.stderr.strip() or "Released checker could not run")
    report = json.loads(process.stdout, object_pairs_hook=records.unique_mapping)
    version = release["version"]
    modern = version_at_least(version, (0, 4, 0))
    expected = {
        "project": name,
        "policyVersion": version,
        "checkerVersion": version,
        "revision": revision,
        "policyRecordsRevision": snapshot["revision"],
        "policyRecordsDigest": snapshot["digest" if modern else "legacyDigest"],
    }
    if (
        not isinstance(report, dict)
        or any(
            key not in report or report[key] != value for key, value in expected.items()
        )
        or report.get("repository", repository) != repository
        or report.get("checkerRevision", release["revision"]) != release["revision"]
        or report.get("status") not in {"pass", "fail", "candidate-ready"}
        or (report["status"] == "fail") != (process.returncode == 1)
        or not isinstance(report.get("issues"), list)
        or any(
            not isinstance(issue, str) or not issue.strip()
            for issue in report["issues"]
        )
        or (report["status"] != "fail" and report["issues"])
        or not declarations.valid_check_names(report.get("dependencies"))
        or any(not records.PROJECT.fullmatch(name) for name in report["dependencies"])
        or (
            version_at_least(version, (0, 3, 0))
            and (
                not report.get("requiredChecks")
                or not declarations.valid_check_names(report["requiredChecks"])
            )
        )
        or (modern and report.get("memberSettings") != settings)
        or (modern and report.get("support") != support_assessment)
    ):
        raise ValueError(
            "Released checker returned an incompatible report or substituted identity"
        )
    return {
        **report,
        "checkerRevision": release["revision"],
        "checkerRepository": release["repository"],
    }
