# Expose development tools through root flakes

Expose each project's development shell, formatter, and project checks through its root `flake.nix`, including the policy repository. This gives consistent native Nix commands and root direnv activation, while accepting that declared development inputs can enter the lock graphs of projects that use these flakes. Project-local functions and modules may supply the implementation; using a project as a dependency does not automatically activate or merge its development shell.
