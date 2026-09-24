"""Test-only external services for the packaged policy scenario.

Loaded by a temporary sitecustomize, never by production checker code. Real
packaged entrypoints execute all policy decisions.
"""

import json
import os
from pathlib import Path
import subprocess
from urllib import request

from tests.fixtures.github import GitHub, request_path


CONFIG = json.loads(Path(os.environ["POLICY_PACKAGE_FIXTURE"]).read_text())
REAL_RUN = subprocess.run
REAL_OUTPUT = subprocess.check_output
STATE = Path(CONFIG["state"])


class PackagedGitHub(GitHub):
    def lookup(self, query):
        path = request_path(query)
        if path in self.responses:
            return super().lookup(query)
        if "/releases/tags/" in path:
            version = path.rsplit("/", 1)[1]
            assert version in CONFIG["releases"], path
            return {
                "tag_name": version,
                "immutable": True,
                "draft": False,
                "prerelease": False,
            }
        if "/git/ref/tags/" in path:
            return {
                "object": {
                    "type": "commit",
                    "sha": CONFIG["releases"][path.rsplit("/", 1)[1]]["revision"],
                }
            }
        return super().lookup(query)


def transport(query, **kwargs):
    return PackagedGitHub.load(STATE).transport(query, **kwargs)


def run(command, **kwargs):
    command = list(map(str, command))
    if Path(command[0]).name != "nix":
        return REAL_RUN(command, **kwargs)
    if command[1] == "run":
        revision = command[3].rsplit("/", 1)[1]
        assert command[1:4] == [
            "run",
            "--no-update-lock-file",
            f"github:{CONFIG['policyRepository']}/{revision}",
        ], command
        release = next(
            value
            for value in CONFIG["releases"].values()
            if value["revision"] == revision
        )
        arguments = command[command.index("--") + 1 :]
        return REAL_RUN([release["program"], *arguments], **kwargs)
    with Path(CONFIG["commands"]).open("a") as stream:
        stream.write(json.dumps(command) + "\n")
    output = ""
    if command[1:3] == ["flake", "metadata"]:
        graph = json.loads((Path(command[3]) / "flake.lock").read_text())
        if "--override-input" in command:
            root = graph["nodes"][graph["root"]]
            graph["nodes"][root["inputs"]["nixpkgs"]]["locked"]["rev"] = command[
                -1
            ].rsplit("/", 1)[1]
        output = json.dumps({"locks": graph})
    elif command[1] == "eval":
        output = (
            os.environ.get("POLICY_PACKAGE_SYSTEM", CONFIG["system"])
            if "--impure" in command
            else '["behavior"]'
        )
    elif command[1] not in {"develop", "fmt", "build"} and command[1:3] != [
        "flake",
        "check",
    ]:
        raise AssertionError(f"Unconfigured Nix operation: {command}")
    if not kwargs.get("text"):
        output = output.encode()
    return subprocess.CompletedProcess(
        command, 0, output, "" if kwargs.get("text") else b""
    )


def check_output(command, **kwargs):
    if Path(command[0]).name == "nix":
        return run(command, **kwargs).stdout
    return REAL_OUTPUT(command, **kwargs)


request.urlopen = transport
subprocess.run = run
subprocess.check_output = check_output
