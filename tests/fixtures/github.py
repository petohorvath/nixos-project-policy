"""Read-only GitHub responses for the packaged audit scenario."""

import io
import json


def request_path(request):
    return request.full_url.removeprefix("https://api.github.com/").split("?")[0]


class GitHub:
    def __init__(self, responses):
        self.responses = responses

    @classmethod
    def load(cls, path):
        return cls(json.loads(path.read_text())["github"])

    def lookup(self, request):
        path = request_path(request)
        if path in self.responses:
            return self.responses[path]
        raise AssertionError(f"Unconfigured external request: {path}")

    def transport(self, request, **kwargs):
        assert request.get_method() == "GET", request.get_method()
        return io.BytesIO(json.dumps(self.lookup(request)).encode())
