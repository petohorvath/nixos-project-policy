"""Test-only external services for the packaged release-transition scenario.

Loaded by a temporary sitecustomize, never by production checker code. Real
packaged entrypoints and immutable legacy sources execute all policy decisions.
"""

import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib import request


CONFIG = json.loads(Path(os.environ["POLICY_TRANSITION_FIXTURE"]).read_text())
REAL_RUN = subprocess.run
REAL_OUTPUT = subprocess.check_output
STATE = Path(CONFIG["state"])


def transport(query, **kwargs):
    path = query.full_url.removeprefix("https://api.github.com/").split("?")[0]
    state = json.loads(STATE.read_text())
    method = query.get_method()
    payload = json.loads(query.data) if query.data else None
    state["requests"].append([method, path, payload])
    if method == "POST" and path.endswith("/check-runs"):
        value = {
            **payload,
            "id": len(state["checks"]) + 1,
            "app": {"slug": "github-actions"},
        }
        state["checks"].append(value)
    elif "/check-runs/" in path:
        value = state["checks"][int(path.rsplit("/", 1)[1]) - 1]
        if method == "PATCH":
            value.update(payload)
    elif path in state["github"]:
        value = state["github"][path]
    elif "/releases/tags/" in path:
        version = path.rsplit("/", 1)[1]
        assert version in CONFIG["releases"], path
        value = {
            "tag_name": version,
            "immutable": True,
            "draft": False,
            "prerelease": False,
        }
    elif "/git/ref/tags/" in path:
        value = {
            "object": {
                "type": "commit",
                "sha": CONFIG["releases"][path.rsplit("/", 1)[1]]["revision"],
            }
        }
    elif "/contents/policy/requirements.json" in path:
        revision = query.full_url.rsplit("ref=", 1)[1]
        release = next(
            value
            for value in CONFIG["releases"].values()
            if value["revision"] == revision
        )
        value = {
            "encoding": "base64",
            "content": base64.b64encode(
                Path(release["requirements"]).read_bytes()
            ).decode(),
        }
    elif "/git/commits/" in path:
        name, revision = path.split("/")[2], path.rsplit("/", 1)[1]
        process = REAL_RUN(
            [
                "git",
                "-C",
                CONFIG["members"][name],
                "cat-file",
                "-e",
                revision + "^{commit}",
            ],
            capture_output=True,
        )
        assert process.returncode == 0, path
        value = {"sha": revision}
    elif "/commits/" in path and path.endswith("/check-runs"):
        value = {
            "check_runs": [
                {
                    "name": "Member / Extra",
                    "head_sha": path.split("/")[-2],
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }
    elif path.endswith("/check-runs"):
        value = {"check_runs": state["checks"]}
    else:
        raise AssertionError(f"Unconfigured external request: {method} {path}")
    STATE.write_text(json.dumps(state))
    if isinstance(value, dict) and set(value) == {"binary"}:
        return io.BytesIO(bytes.fromhex(value["binary"]))
    return io.BytesIO(json.dumps(value).encode())


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
        if "program" in release:
            return REAL_RUN([release["program"], *arguments], **kwargs)
        bootstrap = (
            "import runpy; runpy.run_path("
            + repr(release["source"])
            + ", run_name='__main__', init_globals={'PACKAGED_REVISION': "
            + repr(revision)
            + "})"
        )
        return REAL_RUN([sys.executable, "-c", bootstrap, *arguments], **kwargs)
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
            os.environ.get("POLICY_TRANSITION_SYSTEM", CONFIG["system"])
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
