"""External adapters and local workspaces for executing real workflow shell steps."""

import os
import sys
from unittest.mock import patch

from tests.fixtures.audits import checked_process
from tests.fixtures.data import SOURCE
from tests.fixtures.services import published
from tools import policy, releases


def audit_adapter():
    def gates(project, checks, *, workflow):
        if os.environ["AUDIT_TEST_MODE"] == "github-error":
            raise ValueError("GitHub inspection unavailable; settings are unknown")
        if project["repository"] != "owner/example":
            raise AssertionError("Member declaration redirected GitHub inspection")
        if workflow != ".github/workflows/policy.yml":
            raise AssertionError("GitHub inspection ignored the discovered caller")
        return []

    with (
        patch.object(policy, "git_revision", return_value=SOURCE),
        patch.object(policy, "git_dirty", return_value=False),
        patch.object(releases, "public_get", side_effect=published),
        patch.object(releases.subprocess, "run", side_effect=checked_process),
        patch.object(policy, "check_github", side_effect=gates),
    ):
        sys.exit(policy.main(sys.argv[sys.argv.index("--") + 1 :]))
