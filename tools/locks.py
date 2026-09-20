"""Resolve committed flake lock graphs and repository identities."""

import re
from urllib.parse import urlsplit

REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class LockGraph:
    def __init__(self, lock):
        if not isinstance(lock, dict) or lock.get("version") != 7:
            raise ValueError("Unsupported flake lock format")
        self.nodes = lock["nodes"]
        self.root = lock["root"]
        if not isinstance(self.nodes, dict) or not isinstance(self.root, str):
            raise ValueError("Invalid lock nodes or root")
        if self.root not in self.nodes:
            raise ValueError("Missing lock root")
        for node in self.nodes.values():
            if not isinstance(node, dict) or any(
                not isinstance(node.get(field, {}), dict)
                for field in ("inputs", "locked", "original")
            ):
                raise ValueError("Invalid lock node structure")

    def resolve(self, reference, aliases=()):
        if isinstance(reference, str):
            if reference not in self.nodes:
                raise ValueError(f"Missing lock node: {reference}")
            return reference
        if not isinstance(reference, list) or not all(
            isinstance(item, str) for item in reference
        ):
            raise ValueError("Invalid lock input reference")
        path = tuple(reference)
        if path in aliases:
            raise ValueError(f"Cyclic follows path: {reference}")
        current = self.root
        for part in path:
            inputs = self.nodes[current].get("inputs", {})
            if part not in inputs:
                raise ValueError(f"Missing follows path: {reference}")
            current = self.resolve(inputs[part], (*aliases, path))
        return current

    def reachable(self):
        result = {}
        pending = [self.root]
        while pending:
            current = pending.pop()
            if current in result:
                continue
            result[current] = self.nodes[current]
            pending.extend(
                self.resolve(reference)
                for reference in self.nodes[current].get("inputs", {}).values()
            )
        return result


def dependency_cycles(graph):
    visited = set()
    cycles = []

    def visit(node, stack):
        if node in stack:
            cycles.append([*stack[stack.index(node) :], node])
        elif node not in visited:
            for dependency in graph.get(node, []):
                visit(dependency, [*stack, node])
            visited.add(node)

    for node in graph:
        visit(node, [])
    return cycles


def repository_identity(node):
    for source in [node.get("locked", {}), node.get("original", {})]:
        host = source.get("host", "github.com")
        if (
            source.get("type") in {None, "github"}
            and isinstance(host, str)
            and host.lower() == "github.com"
            and "owner" in source
            and "repo" in source
        ):
            return f"{source['owner']}/{source['repo']}".lower()
        if source.get("type") == "github":
            continue
        url = source.get("url", "")
        scp = re.fullmatch(r"(?:git@)?github\.com:([^/]+/[^/?#]+)/*", url)
        parsed = urlsplit(url)
        path = (scp.group(1) if scp else parsed.path.removeprefix("/")).rstrip("/")
        if (scp or parsed.hostname == "github.com") and REPOSITORY.fullmatch(path):
            return path.removesuffix(".git").lower()
    return None
