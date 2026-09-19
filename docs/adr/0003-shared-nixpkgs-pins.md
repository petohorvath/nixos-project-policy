# Share exact nixpkgs revisions across project uses

Superseded for members selecting v0.2.0 or later by [ADR 0006](0006-independent-selection-and-compatibility.md). Members on earlier releases retain this decision.

Use the same approved stable and unstable nixpkgs revisions wherever member projects select those dependencies, including development shells, tests, package builds, and examples. Approve updates only after all affected projects pass, and complete the separate merges as a tracked batch: this reduces differences between projects while accepting that a failure in one project can delay the shared update. External consumers may select their own dependencies, and existing releases retain their recorded pins.
