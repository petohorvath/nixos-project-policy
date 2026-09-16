# Keep policy checks separate from project flakes

Each member project owns its root flake, development shell, and lockfiles without importing Nix code or pins from the policy repository. Keep shared requirements and checking code in the policy repository, and invoke its approved checks from a small GitHub Actions workflow in each member project. This preserves independent development and builds while accepting repeated local configuration and a CI reference to the shared checking implementation.
