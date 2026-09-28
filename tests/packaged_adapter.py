"""Test-only Nix process adapter for the packaged policy scenario.

Loaded by a temporary sitecustomize, never by production checker code. Real
packaged entrypoints execute all policy decisions.
"""

import json
import os
from pathlib import Path
import subprocess


CONFIG = json.loads(Path(os.environ["POLICY_PACKAGE_FIXTURE"]).read_text())
REAL_RUN = subprocess.run
REAL_OUTPUT = subprocess.check_output


def run(command, **kwargs):
    command = list(map(str, command))
    if Path(command[0]).name != "nix":
        return REAL_RUN(command, **kwargs)
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


subprocess.run = run
subprocess.check_output = check_output
