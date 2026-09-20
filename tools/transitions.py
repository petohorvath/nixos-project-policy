"""Review legacy-record removals against prior trusted enrollment and releases."""

import json

if __package__:
    from . import declarations, records, releases, support
else:
    import declarations
    import records
    import releases
    import support


def validate_legacy_removal(
    previous_root, current_root, workspace, config, pins, *, git_revision, git_dirty
):
    previous = records.read_json(previous_root / "policy/projects.json")
    previous_pins = records.read_json(previous_root / "policy/pins.json")
    previous_members = records.load_members(previous_root, previous["projects"])
    if previous["policyRepository"] != config["policyRepository"]:
        raise ValueError(
            "A legacy transition cannot substitute the trusted policy repository"
        )
    changes = support.legacy_removals(previous, previous_pins, config, pins)
    result = {
        "status": "valid",
        "legacyCleanup": "not-needed",
        "legacyRemovals": changes,
        "issues": [],
        "migrationEvidence": [],
    }
    if not changes:
        return result
    result["legacyCleanup"] = "blocked"
    decisions = config["_support"]
    # The built-in floor avoids needing network access to reject unsafe routine removals.
    versions = set(support.KNOWN_LEGACY_RELEASES)
    supported = sorted(
        version
        for version in versions
        if support.assess(version, decisions)["status"] == "supported"
    )
    if not supported:
        published = set(releases.published_versions(config["policyRepository"]))
        if not versions <= published:
            raise ValueError("Published policy release inventory is incomplete")
        versions.update(
            version
            for version in published
            if not releases.version_at_least(version, (0, 4, 0))
        )
        supported = sorted(
            version
            for version in versions
            if support.assess(version, decisions)["status"] == "supported"
        )
    if supported:
        result.update(
            status="fail",
            issues=[
                "Legacy records are still required by supported releases: "
                + ", ".join(supported)
            ],
        )
        return result
    members = dict(previous_members)
    for name, repository in config["_members"].items():
        if name in members and members[name].lower() != repository.lower():
            raise ValueError(
                "A legacy transition cannot substitute an enrolled repository"
            )
        members[name] = repository
    if members and workspace is None:
        raise ValueError(
            "Legacy cleanup requires --workspace with exact migrated member checkouts"
        )
    snapshot = records.identity(config, pins, git_revision(current_root))
    for name, repository in members.items():
        root = workspace / name
        revision = git_revision(root)
        if (
            not isinstance(revision, str)
            or not releases.REVISION.fullmatch(revision)
            or git_dirty(root)
        ):
            raise ValueError(
                f"Legacy cleanup cannot inspect the exact clean member commit: {name}"
            )
        _, _, caller, version = declarations.read_identity(
            root, config["policyRepository"], name
        )
        inputs = caller["with"]
        assessment = support.assess(version, decisions)
        if (
            not releases.version_at_least(version, (0, 4, 0))
            or assessment["status"] != "supported"
        ):
            result["status"] = "fail"
            result["issues"].append(
                f"Legacy records remain needed by {name} selecting {version}"
            )
            continue
        release = releases.inspect_release(config["policyRepository"], version)
        settings = {
            field: json.loads(inputs.get(key, "[]"))
            for key, field in declarations.INPUT_FIELDS.items()
        }
        report = releases.check_member(
            release,
            root,
            name,
            repository,
            revision,
            current_root,
            snapshot,
            settings=settings,
            support_assessment=assessment,
        )
        if report["status"] != "pass":
            result["status"] = "fail"
            result["issues"].append(
                f"Legacy cleanup requires a passing migrated declaration: {name}"
            )
        if git_revision(root) != revision or git_dirty(root):
            raise ValueError(f"Member changed during legacy cleanup inspection: {name}")
        result["migrationEvidence"].append(
            {
                "project": name,
                "repository": repository,
                "revision": revision,
                "checkerRevision": release["revision"],
                "support": assessment,
            }
        )
    result["records"] = snapshot
    if result["status"] == "valid":
        result["legacyCleanup"] = "eligible"
    return result
