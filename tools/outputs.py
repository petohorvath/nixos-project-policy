"""Public-output rules for a repo's flake; see POLICY.md "Public outputs".

Public outputs are every top-level flake output except `checks`, `devShells`,
and `formatter`. `tools/outputs.nix` names and evaluates them.

The removal comparison uses the highest `vMAJOR.MINOR.PATCH` Git tag reachable
from HEAD. The tag's tree is exported with `git archive` into a temporary
directory and evaluated as a `path:` flake, so the repo is never fetched. A
shallow clone may lack that tag, so the comparison fails there instead.
"""

import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

if __package__:
    from . import findings as rule_findings, releases
else:
    import findings as rule_findings
    import releases


# Stable rule ids, in report order. Each id is one column in a survey table.
RULES = ("outputs-evaluate", "outputs-empty", "outputs-removal")
FIRST_TAG = "v0.1.0"
EXPRESSION_PATH = Path(__file__).resolve().with_name("outputs.nix")
FLAKE_VARIABLE = "NIXOS_PROJECT_POLICY_OUTPUTS_FLAKE"
EXPRESSION = f"""
let
  getEnv = name: builtins.getEnv "NIXOS_PROJECT_POLICY_OUTPUTS_${{name}}";
in
import (/. + getEnv "EXPRESSION") {{
  flake = builtins.getFlake (builtins.getEnv "{FLAKE_VARIABLE}");
  system = getEnv "SYSTEM";
  mode = getEnv "MODE";
  output = getEnv "OUTPUT";
}}
"""
# A release heading names a version first: `0.5.0`, `v0.5.0`, `[0.5.0] - date`.
RELEASE_HEADING = re.compile(
    rf"#{{1,6}}\s+\[?v?{releases.CORE}(?:[-+][0-9A-Za-z.+-]*)?\]?(?:\s|$)"
)


class EvaluationError(Exception):
    """Nix could not evaluate a flake's outputs."""


def check(root, system):
    """Apply the public-output rules to the flake at ROOT on SYSTEM.

    Returns (findings, rules, release). Findings name the public output path
    in `output`. `release` holds the last release `tag` and the changelog
    `version`, either of which can be None.
    """
    findings = []
    try:
        public = evaluate(root, system, "list")
    except EvaluationError as error:
        findings.append(
            finding(
                "outputs-evaluate",
                None,
                "fail",
                f"flake outputs do not evaluate: {error}",
            )
        )
        rules = {"outputs-evaluate": "fail"}
        rules.update(dict.fromkeys(RULES[1:], "not-run"))
        return findings, rules, {"tag": None, "version": None}
    names = set()
    unknown = set()
    for output in public:
        try:
            described = evaluate(root, system, "evaluate", output)[output]
        except EvaluationError as error:
            unknown.add(output)
            findings.append(
                finding(
                    "outputs-evaluate",
                    output,
                    "fail",
                    f"public output does not evaluate: {error}",
                )
            )
            continue
        names.update(described["names"])
        findings.extend(
            finding("outputs-evaluate", path, "fail", "public output does not evaluate")
            for path in described["failed"]
        )
        findings.extend(
            finding(
                "outputs-empty",
                path,
                "notice",
                "empty output namespace; do not publish it",
            )
            for path in described["empty"]
        )
    removal, release = compare_release(root, system, names, unknown)
    findings.extend(removal)
    rules = rule_findings.summarize(findings, RULES)
    if rules["outputs-removal"] == "notice":
        # Only a missing release tag yields a removal notice.
        rules["outputs-removal"] = "not-run"
    return findings, rules, release


def compare_release(root, system, names, unknown):
    """Compare public output names with the last release tag.

    Names under an output in UNKNOWN count as present: that output failed to
    evaluate, and the outputs-evaluate rule reports it.
    """
    version = changelog_version(root)
    if shallow(root):
        return [
            finding(
                "outputs-removal",
                None,
                "fail",
                "shallow clone, so the last release tag cannot be found; "
                "fetch the full history and tags",
            )
        ], {"tag": None, "version": version}
    tag = last_release_tag(root)
    release = {"tag": tag, "version": version}
    if tag is None:
        return [
            finding(
                "outputs-removal",
                None,
                "notice",
                "no release tag, so the removal comparison is skipped; "
                f"tag a first release, such as {FIRST_TAG}, to turn it on",
            )
        ], release
    try:
        previous = tagged_names(root, system, tag)
    except EvaluationError as error:
        return [
            finding(
                "outputs-removal",
                None,
                "fail",
                f"cannot list public outputs at {tag}: {error}",
            )
        ], release
    removed = sorted(
        name for name in previous - names if name.split(".", 1)[0] not in unknown
    )
    if not removed or bumped(tag, version):
        return [], release
    if version is None:
        reason = "CHANGELOG.md has no release heading"
    else:
        reason = f"the topmost CHANGELOG.md release is {version}"
    return [
        finding(
            "outputs-removal",
            name,
            "fail",
            f"public output removed since {tag} without a minor or major "
            f"version bump; {reason}",
        )
        for name in removed
    ], release


def bumped(tag, version):
    if version is None:
        return False
    old = [int(part) for part in releases.TAG.fullmatch(tag).groups()]
    new = [int(part) for part in version.split(".")]
    return new[0] > old[0] or (new[0] == old[0] and new[1] > old[1])


def shallow(root):
    """Return whether ROOT is a shallow Git clone; False outside Git."""
    try:
        return (
            releases.git(root, "rev-parse", "--is-shallow-repository").strip() == "true"
        )
    except subprocess.CalledProcessError:
        return False


def last_release_tag(root):
    """Return the highest `vMAJOR.MINOR.PATCH` tag reachable from HEAD."""
    try:
        listed = releases.git(root, "tag", "--merged", "HEAD")
    except subprocess.CalledProcessError:
        return None
    tags = [
        (tuple(int(part) for part in match.groups()), match.string)
        for match in map(releases.TAG.fullmatch, listed.split())
        if match
    ]
    return max(tags)[1] if tags else None


def changelog_version(root):
    """Return the topmost release heading's version in ROOT/CHANGELOG.md."""
    try:
        text = (root / "CHANGELOG.md").read_text()
    except FileNotFoundError:
        return None
    for line in text.splitlines():
        match = RELEASE_HEADING.match(line)
        if match:
            return ".".join(match.groups())
    return None


def tagged_names(root, system, tag):
    """Return the public output names of the flake at TAG."""
    prefix = releases.git(root, "rev-parse", "--show-prefix").strip()
    archive = releases.git(
        root, "archive", "--format=tar", f"{tag}:{prefix}", text=False
    )
    with tempfile.TemporaryDirectory(prefix="policy-release-") as directory:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(directory, filter="data")
        if not (Path(directory) / "flake.nix").exists():
            return set()
        described = evaluate(Path(directory), system, "names")
    return {name for output in described.values() for name in output["names"]}


def evaluate(root, system, mode, output=""):
    """Run tools/outputs.nix on the flake at ROOT and return its JSON."""
    result = subprocess.run(
        ["nix", "eval", "--json", "--impure", "--expr", EXPRESSION],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env={
            **os.environ,
            FLAKE_VARIABLE: f"path:{root.resolve()}",
            "NIXOS_PROJECT_POLICY_OUTPUTS_EXPRESSION": str(EXPRESSION_PATH),
            "NIXOS_PROJECT_POLICY_OUTPUTS_SYSTEM": system,
            "NIXOS_PROJECT_POLICY_OUTPUTS_MODE": mode,
            "NIXOS_PROJECT_POLICY_OUTPUTS_OUTPUT": output,
        },
    )
    if result.returncode != 0:
        raise EvaluationError(error_summary(result.stderr))
    value = json.loads(result.stdout)
    if mode == "list":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError("public output listing returned malformed names")
        return value
    if not isinstance(value, dict) or not all(
        isinstance(described, dict)
        and all(
            isinstance(described.get(key), list)
            and all(isinstance(item, str) for item in described[key])
            for key in ("names", "empty", "failed")
        )
        for described in value.values()
    ):
        raise ValueError("public output evaluation returned a malformed result")
    return value


def error_summary(stderr):
    """Return Nix's last error message from STDERR on one line."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    starts = [index for index, line in enumerate(lines) if line.startswith("error:")]
    summary = " ".join(lines[starts[-1] :] if starts else lines[-1:])
    return summary[:500] or "nix eval failed"


def finding(rule, output, level, message):
    return rule_findings.finding(rule, level, message, output=output)
