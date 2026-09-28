"""Input rules for a repo's root flake lock; see POLICY.md "Inputs".

Only the root `flake.lock` is read. Independently locked examples and other
nested lock files are not checked, and third-party inputs are unrestricted.
"""

import json
import re

if __package__:
    from . import findings as rule_findings, locks, releases
else:
    import findings as rule_findings
    import locks
    import releases


# Stable rule ids, in report order. Each id is one column in a survey table.
RULES = (
    "lock",
    "root-nixpkgs",
    "extra-nixpkgs",
    "sibling-tag",
    "sibling-one-revision",
    "sibling-follows-nixpkgs",
    "no-policy-input",
)
NIXPKGS = "nixos/nixpkgs"
# A sibling reference to a release tag, such as `v1.2.0` or `refs/tags/1.2.0-rc.1`.
RELEASE_REF = re.compile(rf"(?:refs/tags/)?v?{releases.CORE}(?:[-+][0-9A-Za-z.+-]+)?\Z")


class Siblings:
    """Sibling repos: every GitHub repository under an owner of a listed repo.

    The policy repository is not a sibling; the no-policy-input rule covers it.
    """

    def __init__(self, repos, policy_repository):
        self.owners = {repository.split("/")[0].lower() for repository in repos}
        self.policy_repository = policy_repository.lower()

    def __contains__(self, identity):
        return (
            identity is not None
            and identity != self.policy_repository
            and identity.split("/")[0] in self.owners
        )


def root_nixpkgs(lock):
    """Return (node id, problem) for the root `nixpkgs` input.

    The node id is None when the root has no `nixpkgs` input. The problem is
    None when the input is a locked `NixOS/nixpkgs` commit.
    """
    inputs = lock.nodes[lock.root].get("inputs", {})
    if "nixpkgs" not in inputs:
        return None, "missing root nixpkgs input"
    node_id = lock.resolve(inputs["nixpkgs"])
    node = lock.nodes[node_id]
    locked = node.get("locked", {})
    if (
        locks.repository_identity({"locked": locked}) != NIXPKGS
        or locked.get("type") not in {"github", "git"}
        or locked.get("dir")
        or node.get("flake") is False
    ):
        return node_id, "root nixpkgs must identify the NixOS/nixpkgs flake"
    if not isinstance(locked.get("rev"), str) or not locks.REVISION.fullmatch(
        locked["rev"]
    ):
        return node_id, "root nixpkgs must be locked to an exact commit"
    return node_id, None


def check(root, repos, policy_repository):
    """Apply the input rules to ROOT/flake.lock.

    Returns (findings, siblings). Each finding is a dict with rule, input,
    level ("fail" or "notice"), and message. Inputs are named by their path of
    input names from the root, joined with "/".
    """
    try:
        lock = locks.LockGraph(json.loads((root / "flake.lock").read_text()))
        paths = input_paths(lock)
    except FileNotFoundError:
        return [finding("lock", "flake.lock", "fail", "missing root flake.lock")], []
    except (ValueError, KeyError, TypeError) as error:
        return [finding("lock", "flake.lock", "fail", str(error))], []
    sibling_repos = Siblings(repos, policy_repository)
    findings = []
    nixpkgs, problem = root_nixpkgs(lock)
    if problem is not None:
        findings.append(finding("root-nixpkgs", "nixpkgs", "fail", problem))
    for name, reference in sorted(lock.nodes[lock.root].get("inputs", {}).items()):
        node_id = lock.resolve(reference)
        if (
            name != "nixpkgs"
            and node_id != nixpkgs
            and locks.repository_identity(lock.nodes[node_id]) == NIXPKGS
        ):
            findings.append(
                finding(
                    "extra-nixpkgs",
                    name,
                    "notice",
                    "additional nixpkgs input; flake.nix must state the reason",
                )
            )
        identity = locks.repository_identity(lock.nodes[node_id])
        if identity in sibling_repos:
            findings.extend(reference_findings(name, lock.nodes[node_id]))
    siblings = []
    revisions = {}
    for node_id, path in paths.items():
        node = lock.nodes[node_id]
        identity = locks.repository_identity(node)
        if identity == policy_repository.lower():
            findings.append(
                finding(
                    "no-policy-input",
                    path,
                    "fail",
                    "the policy repository is a flake input",
                )
            )
        if identity not in sibling_repos:
            continue
        revision = node.get("locked", {}).get("rev") or node.get("locked", {}).get(
            "narHash"
        )
        revisions.setdefault(identity, {}).setdefault(revision, []).append(path)
        siblings.append(
            {
                "input": path,
                "repository": identity,
                "revision": revision,
                "reference": reference_kind(node),
            }
        )
        sibling_nixpkgs = node.get("inputs", {}).get("nixpkgs")
        if sibling_nixpkgs is not None and (
            nixpkgs is None or lock.resolve(sibling_nixpkgs) != nixpkgs
        ):
            findings.append(
                finding(
                    "sibling-follows-nixpkgs",
                    path,
                    "fail",
                    "sibling nixpkgs must follow root nixpkgs",
                )
            )
    for identity, by_revision in sorted(revisions.items()):
        if len(by_revision) > 1:
            located = "; ".join(
                f"{revision}: {', '.join(inputs)}"
                for revision, inputs in sorted(
                    by_revision.items(), key=lambda item: str(item[0])
                )
            )
            findings.append(
                finding(
                    "sibling-one-revision",
                    identity,
                    "fail",
                    f"sibling appears at {len(by_revision)} revisions ({located})",
                )
            )
    return findings, siblings


def reference_kind(node):
    original = node.get("original", {})
    ref = original.get("ref")
    if isinstance(ref, str) and RELEASE_REF.fullmatch(ref):
        return "tag"
    if "rev" in original:
        return "commit"
    return "branch"


def reference_findings(name, node):
    kind = reference_kind(node)
    if kind == "commit":
        return [
            finding(
                "sibling-tag",
                name,
                "notice",
                "temporary commit reference; state the reason in the PR",
            )
        ]
    if kind == "branch":
        ref = node.get("original", {}).get("ref")
        where = f"branch {ref!r}" if ref else "the default branch"
        return [
            finding(
                "sibling-tag",
                name,
                "fail",
                f"sibling references {where}, not a release tag",
            )
        ]
    return []


def input_paths(lock):
    """Name each reachable non-root node by its shortest input path."""
    paths = {}
    queue = [(lock.root, ())]
    while queue:
        node_id, path = queue.pop(0)
        for name, reference in sorted(lock.nodes[node_id].get("inputs", {}).items()):
            child = lock.resolve(reference)
            if child != lock.root and child not in paths:
                paths[child] = "/".join((*path, name))
                queue.append((child, (*path, name)))
    return paths


def finding(rule, input_name, level, message):
    return rule_findings.finding(rule, level, message, input=input_name)


def summarize(findings):
    """Return each rule's status: fail, notice, pass, or not-run.

    Without a readable root lock, only the lock rule runs.
    """
    status = rule_findings.summarize(findings, RULES)
    if status["lock"] == "fail":
        status = {rule: "not-run" for rule in RULES} | {"lock": "fail"}
    return status
