# Project policy

Shared rules for the Nix flake repositories in devnix-labs: what each repo guarantees about its inputs, public outputs, and tests.

## Language

**Repo**:
A Nix flake repository in devnix-labs that the policy applies to.
_Avoid_: Member, member project, project

**Listed repo**:
A repo on the policy's list of repos that must pass; a new stable or unstable pin reaches a release only after every listed repo passes with it.
_Avoid_: Enrolled member, enrollment

**Public outputs**:
Every top-level flake output of a repo except `devShells`, `formatter`, and `checks`.
_Avoid_: Public interface, exports

**Stable pin**:
The exact revision of the stable NixOS nixpkgs branch that a policy release tests every repo against.
_Avoid_: Shared pin, approved pin, pin candidate

**Unstable pin**:
The exact revision of nixos-unstable that a policy release tests every repo against.
_Avoid_: Shared pin, approved pin, pin candidate

**Check**:
The verification of one repo revision against the input rules and public outputs, together with its default development shell and formatter. Tests run separately.
_Avoid_: Audit, compliance, policy check
