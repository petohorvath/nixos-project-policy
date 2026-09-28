"""Propose pin bumps, plan their tests, and plan the patch release they create.

The pin-bump workflows run this helper; it is not part of the checker CLI.
Every command prints JSON and exits 0, or prints an error and exits 2.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

if __package__:
    from . import records
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import records


SOURCE_ROOT = Path(__file__).resolve().parents[1]
RUNNERS = {"x86_64-linux": "ubuntu-24.04", "aarch64-linux": "ubuntu-24.04-arm"}
VM_SYSTEM = "x86_64-linux"
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pin_bump", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    propose = commands.add_parser(
        "propose", help="Write new pins and the next patch version"
    )
    propose.add_argument("--stable", required=True)
    propose.add_argument("--unstable", required=True)
    commands.add_parser("matrix", help="Print the pin-bump test matrix")
    commands.add_parser(
        "release", help="Plan the patch release tag for the current commit"
    )
    for command in commands.choices.values():
        command.add_argument("--root", type=Path, default=SOURCE_ROOT)
    args = parser.parse_args(argv)
    try:
        if args.command == "propose":
            result = propose_pins(args.root, args.stable, args.unstable)
        elif args.command == "matrix":
            result = matrix(records.load_repos(args.root / "data/repos.json"))
        else:
            result = plan_release(args.root)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def propose_pins(root, stable, unstable):
    """Write the pins, the legacy pins file, VERSION, and a changelog entry."""
    pair = {"stable": stable, "unstable": unstable}
    records.validate_pair(pair)
    current = records.load_pins(root / "data/pins.json")
    pins = {**current, **pair}
    version = read_version(root)
    tags = release_tags(root)
    if f"v{version}" not in tags:
        raise ValueError(
            f"VERSION {version} has no release tag v{version}; release it before a pin bump"
        )
    release = next_patch(version, tags)
    result = {"pins": pins, **release}
    if pins == current:
        return {"status": "unchanged", **result}
    write_json(root / "data/pins.json", dict(sorted(pins.items())))
    # Transition: v0.4.0 callers read the old pins file from main.
    write_json(
        root / "policy/pins.json",
        {
            "schemaVersion": 1,
            "approved": pair,
            "stableBranch": pins["stableBranch"],
        },
    )
    (root / "VERSION").write_text(f"{release['version']}\n")
    changelog = root / "CHANGELOG.md"
    changelog.write_text(
        release_changelog(
            changelog.read_text(),
            release["version"],
            f"- Update the stable pin (`{pins['stableBranch']}`) to `{stable}` "
            f"and the unstable pin to `{unstable}`.",
        )
    )
    return {"status": "proposed", **result}


def matrix(repos):
    """One job per listed repo, pin, and system, plus VM tests per repo."""
    include = []
    for repository in repos:
        for task in ["stable", "unstable"]:
            for system, runner in RUNNERS.items():
                include.append(
                    {
                        "repository": repository,
                        "task": task,
                        "system": system,
                        "runner": runner,
                        "name": f"Tests ({task}, {system})",
                    }
                )
        include.append(
            {
                "repository": repository,
                "task": "vm",
                "system": VM_SYSTEM,
                "runner": RUNNERS[VM_SYSTEM],
                "name": "VM tests",
            }
        )
    return {"include": include}


def plan_release(root):
    """Plan the tag for a merged pin bump: VERSION must be the next patch."""
    version = read_version(root)
    tag = f"v{version}"
    revision = git(root, "rev-parse", "HEAD")
    tags = release_tags(root)
    release = {**series(version), "revision": revision}
    if tag in tags:
        tagged = git(root, "rev-list", "-n", "1", f"refs/tags/{tag}")
        if tagged != revision:
            raise ValueError(f"{tag} already exists on {tagged}; tags never move")
        latest = next_patch(version, tags - {tag})["version"]
        if version != latest:
            # Re-running an older release must not move the series tag back.
            raise ValueError(f"{tag} is not the latest release in its series")
        return {"status": "released", **release}
    expected = next_patch(version, tags)["version"]
    if version != expected:
        raise ValueError(f"VERSION {version} is not the next patch release {expected}")
    return {"status": "release", **release}


def next_patch(version, tags):
    """Next patch after the highest vX.Y.Z tag in VERSION's minor series."""
    major, minor, _ = parse_version(version)
    patches = []
    for tag in tags:
        parsed = VERSION.fullmatch(tag[1:]) if tag.startswith("v") else None
        if parsed and tuple(map(int, parsed.groups()[:2])) == (major, minor):
            patches.append(int(parsed.group(3)))
    if not patches:
        raise ValueError(
            f"No v{major}.{minor}.Z release tag for VERSION {version}; "
            "tag the minor release by hand first"
        )
    return series(f"{major}.{minor}.{max(patches) + 1}")


def series(version):
    major, minor, _ = parse_version(version)
    return {"version": version, "tag": f"v{version}", "seriesTag": f"v{major}.{minor}"}


def parse_version(version):
    parsed = VERSION.fullmatch(version)
    if not parsed:
        raise ValueError(f"VERSION must be MAJOR.MINOR.PATCH, not {version!r}")
    return tuple(map(int, parsed.groups()))


def read_version(root):
    version = (root / "VERSION").read_text().strip()
    parse_version(version)
    return version


def release_tags(root):
    return set(git(root, "tag", "--list", "v*").split())


def release_changelog(text, version, entry):
    """Move unreleased entries under a new version heading with the entry."""
    lines = text.splitlines()
    headings = [index for index, line in enumerate(lines) if line.startswith("## ")]
    if not headings:
        raise ValueError("CHANGELOG.md needs a version or Unreleased heading")
    start = headings[0]
    moved = []
    if lines[start] == "## Unreleased":
        end = headings[1] if len(headings) > 1 else len(lines)
        moved = "\n".join(lines[start + 1 : end]).strip("\n").splitlines()
        lines[start + 1 : end] = [""]
        start += 2
    lines[start:start] = [f"## {version}", "", entry, *moved, ""]
    return "\n".join(lines) + "\n"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def git(root, *arguments):
    return subprocess.check_output(
        ["git", "-C", str(root), *arguments], text=True
    ).strip()


if __name__ == "__main__":
    sys.exit(main())
