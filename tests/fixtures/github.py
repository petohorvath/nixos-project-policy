"""In-memory GitHub responses with portable workflow artifacts and check runs."""

import hashlib
import io
import json
import zipfile


def request_path(request):
    return request.full_url.removeprefix("https://api.github.com/").split("?")[0]


class GitHub:
    def __init__(self, responses=None, checks=None, requests=None):
        self.responses = responses if responses is not None else {}
        self.checks = checks if checks is not None else []
        self.requests = requests if requests is not None else []

    @classmethod
    def load(cls, path):
        state = json.loads(path.read_text())
        responses = {
            key: bytes.fromhex(value["binary"])
            if isinstance(value, dict) and set(value) == {"binary"}
            else value
            for key, value in state["github"].items()
        }
        return cls(responses, state["checks"], state.get("requests", []))

    def save(self, path):
        path.write_text(
            json.dumps(
                {
                    "github": {
                        key: {"binary": value.hex()}
                        if isinstance(value, bytes)
                        else value
                        for key, value in self.responses.items()
                    },
                    "checks": self.checks,
                    "requests": self.requests,
                }
            )
        )

    def lookup(self, request):
        path = request_path(request)
        if path in self.responses:
            return self.responses[path]
        if path.endswith("/check-runs"):
            return {"check_runs": self.checks}
        raise AssertionError(
            f"Unconfigured external request: {request.get_method()} {path}"
        )

    def transport(self, request, **kwargs):
        path = request_path(request)
        method = request.get_method()
        payload = json.loads(request.data) if request.data else None
        self.requests.append((method, path, payload))
        if method == "POST" and path.endswith("/check-runs"):
            value = {
                **payload,
                "id": len(self.checks) + 1,
                "app": {"slug": "github-actions"},
            }
            self.checks.append(value)
        elif "/check-runs/" in path:
            value = self.checks[int(path.rsplit("/", 1)[1]) - 1]
            if method == "PATCH":
                value.update(payload)
        else:
            value = self.lookup(request)
        return io.BytesIO(
            value if isinstance(value, bytes) else json.dumps(value).encode()
        )

    def pin_pr(self, repository, base, head):
        self.responses.update(
            {
                f"repos/{repository}": {
                    "full_name": repository,
                    "default_branch": "main",
                },
                f"repos/{repository}/pulls/7": {
                    "number": 7,
                    "state": "open",
                    "head": {"sha": head},
                    "base": {
                        "sha": base,
                        "ref": "main",
                        "repo": {"full_name": repository},
                    },
                },
                f"repos/{repository}/git/ref/heads/main": {"object": {"sha": base}},
                f"repos/{repository}/actions/runs/91": {
                    "id": 91,
                    "run_attempt": 2,
                    "event": "pull_request_target",
                    "path": ".github/workflows/pin-pr.yml",
                    "head_sha": base,
                    "repository": {"full_name": repository},
                },
            }
        )

    def artifact(self, repository, base, name, directory):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for path in directory.rglob("*"):
                if path.is_file():
                    archive.writestr(
                        str(path.relative_to(directory)), path.read_bytes()
                    )
        data = stream.getvalue()
        entries = self.responses.setdefault(
            f"repos/{repository}/actions/runs/91/artifacts", {"artifacts": []}
        )["artifacts"]
        identity = len(entries) + 1
        entries.append(
            {
                "id": identity,
                "name": name,
                "expired": False,
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "workflow_run": {"id": 91, "head_sha": base},
            }
        )
        self.responses[f"repos/{repository}/actions/artifacts/{identity}/zip"] = data
