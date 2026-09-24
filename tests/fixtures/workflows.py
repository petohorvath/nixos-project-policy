"""External adapters and local workspaces for executing real workflow shell steps."""

import os
from pathlib import Path
import sys
from unittest.mock import patch

from tests.fixtures.audits import checked_process
from tests.fixtures.data import SOURCE
from tests.fixtures.github import GitHub
from tests.fixtures.process import REAL_RUN
from tests.fixtures.services import BatchServices, Services, published
from tools import policy, releases


def environment(workspace, *, adapter=None, program=None, inherited=None):
    inherited = os.environ if inherited is None else inherited
    binary = workspace / "bin"
    binary.mkdir()
    if program is not None:
        body = (
            "import os,sys\n"
            "assert sys.argv[1:4] == ['run','--no-update-lock-file','./authority']\n"
            f"os.execv({program!r}, [{program!r}, *sys.argv[sys.argv.index('--')+1:]])\n"
        )
    else:
        body = (
            "import sys\n"
            f"sys.path.insert(0, {str(policy.SOURCE_ROOT)!r})\n"
            f"from tests.fixtures.workflows import {adapter}\n{adapter}()\n"
        )
    stub = binary / "nix"
    stub.write_text(f"#!{sys.executable}\n{body}")
    stub.chmod(0o755)
    return {
        **inherited,
        "PATH": f"{binary}:{inherited['PATH']}",
        "RUNNER_TEMP": str(workspace),
        "GITHUB_OUTPUT": str(workspace / "github-output"),
    }


def run_step(workflow, job, workspace, environment, *, match="nix run", **extra):
    step = next(
        step for step in workflow["jobs"][job]["steps"] if match in step.get("run", "")
    )
    return REAL_RUN(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=workspace,
        env={**environment, **extra},
        text=True,
        capture_output=True,
    )


def candidate_adapter():
    services = Services(Path(os.environ["CANDIDATE_FIXTURE_ROOT"]))
    services.host = os.environ.get("SYSTEM", "x86_64-linux")
    services.failure = os.environ.get("CANDIDATE_FIXTURE_FAILURE")
    with services.installed():
        sys.exit(policy.main(sys.argv[sys.argv.index("--") + 1 :]))


def batch_adapter():
    import json

    services = BatchServices(
        {
            name: Path(root)
            for name, root in json.loads(os.environ["BATCH_FIXTURE_ROOTS"]).items()
        }
    )
    services.host = os.environ.get("SYSTEM", "x86_64-linux")
    services.failure = os.environ.get("BATCH_FIXTURE_FAILURE")
    services.failed_member = os.environ.get("BATCH_FIXTURE_FAILED_MEMBER")
    with services.installed():
        sys.exit(policy.main(sys.argv[sys.argv.index("--") + 1 :]))


def pr_adapter():
    if sys.argv[1:3] != ["run", "--no-update-lock-file"] or sys.argv[3] not in {
        "./authority",
        ".#",
    }:
        raise AssertionError("Workflow substituted the trusted coordinator")
    state = Path(os.environ["PR_FIXTURE_STATE"])
    github = GitHub.load(state)
    services = Services(Path(os.environ["PR_FIXTURE_ROOT"]))
    services.host = os.environ.get("SYSTEM", "x86_64-linux")
    with (
        patch("urllib.request.urlopen", side_effect=github.transport),
        services.installed(coordinator=os.environ["PR_FIXTURE_BASE"]),
    ):
        code = policy.main(sys.argv[sys.argv.index("--") + 1 :])
    github.save(state)
    sys.exit(code)


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
