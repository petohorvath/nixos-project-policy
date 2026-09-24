"""Controlled immutable-release responses for audits."""

from tests.fixtures.data import CHECKER


def published(path):
    if "/releases/tags/" in path:
        return {
            "tag_name": path.rsplit("/", 1)[1],
            "immutable": True,
            "draft": False,
            "prerelease": False,
        }
    return {"object": {"type": "commit", "sha": CHECKER}}
