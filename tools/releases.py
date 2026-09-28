"""Git access and SemVer release versions and tags."""

import re
import subprocess

NUMBER = r"(0|[1-9][0-9]*)"
# MAJOR.MINOR.PATCH; the groups are the three numbers.
CORE = rf"{NUMBER}\.{NUMBER}\.{NUMBER}"
VERSION = re.compile(rf"{CORE}\Z")
# A release tag: `v` and a version.
TAG = re.compile(rf"v{CORE}\Z")


def git(root, *arguments, text=True):
    """Run git in ROOT and return its standard output; raise on failure."""
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=True,
    ).stdout
