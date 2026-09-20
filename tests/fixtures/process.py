"""Unpatched process entrypoints for fixtures that also adapt subprocess calls."""

import contextlib
import os
import subprocess
from unittest.mock import patch

REAL_RUN = subprocess.run
REAL_OUTPUT = subprocess.check_output


@contextlib.contextmanager
def isolated_git(workspace):
    configuration = workspace / "gitconfig"
    configuration.write_text("")
    with patch.dict(
        os.environ,
        {
            "GIT_CONFIG_GLOBAL": str(configuration),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "file:https:ssh",
        },
    ):
        yield


def git(root, *arguments):
    return REAL_OUTPUT(
        ["git", "-C", str(root), *arguments], text=True, stderr=subprocess.PIPE
    ).strip()


def initialize(root):
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "Policy fixture")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "core.hooksPath", "/dev/null")


def commit(root):
    if not (root / ".git").exists():
        initialize(root)
    git(root, "add", ".")
    git(root, "commit", "--quiet", "--allow-empty", "-m", "Fixture revision")
    return git(root, "rev-parse", "HEAD")
