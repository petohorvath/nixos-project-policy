# Domain docs

This repository uses a single-context layout: the root [CONTEXT.md](../../CONTEXT.md) holds the glossary, and [docs/adr/](../adr/) holds architecture decisions.

## Read before exploration

Read `CONTEXT.md` before exploring the codebase. Read ADRs relevant to the area being investigated or changed.

If a glossary or ADR directory is absent, proceed silently. Create domain documentation when terms or decisions are resolved.

## Vocabulary

Use the terms defined in `CONTEXT.md` in issue titles, proposals, hypotheses, tests, and documentation. Follow the glossary's preferred terms and avoid the synonyms it identifies.

When a needed concept is missing, check whether an existing term covers it. Record a real terminology gap for domain modeling.

## ADR conflicts

When a proposal contradicts an existing ADR, identify the ADR and explain why the decision should be reconsidered. Make the conflict explicit before implementing the proposal.
