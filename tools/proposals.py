"""Validate pin proposals against trusted records without granting approval."""

import copy
import re

if __package__:
    from . import records
else:
    import records


def read(baseline, proposal, *, git_revision):
    if baseline.resolve() == proposal.resolve():
        trusted = records.proposed_snapshot(baseline)
    else:
        config, pins = records.load(baseline)
        trusted = config, pins, records.identity(config, pins, git_revision(baseline))
    return Proposal(trusted, records.proposed_snapshot(proposal))


class Proposal:
    def __init__(self, baseline, proposed):
        self.config, self.pins, self.baseline = baseline
        self.proposed, self.proposed_pins, self.proposal = proposed
        self.before = {batch["id"]: batch for batch in self.pins["batches"]}

    def _validate_candidate(self, batch):
        # A proposal cannot replace the authority used to assess its candidate.
        if (
            self.config != self.proposed
            or self.pins["stableBranch"] != self.proposed_pins["stableBranch"]
        ):
            raise ValueError(
                "Pin proposals cannot alter trusted enrollment or policy records"
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", batch["id"]):
            raise ValueError("Candidate batch needs a simple identifier")
        if batch["status"] == "withdrawn":
            raise ValueError("Proposal must identify one active candidate batch")
        existing = self.before.get(batch["id"])
        if existing and existing["status"] != "candidate":
            raise ValueError(
                "Approved or historical batches cannot be reused as candidates"
            )

    def candidate(self, batch_id):
        """Return execution pins retaining baseline approval and the selected batch."""
        selected = [
            batch for batch in self.proposed_pins["batches"] if batch["id"] == batch_id
        ]
        if len(selected) != 1:
            raise ValueError("Proposal must identify one active candidate batch")
        batch = selected[0]
        self._validate_candidate(batch)
        if (
            batch["status"] == "complete"
            and self.proposed_pins["approved"] != batch["pins"]
        ):
            raise ValueError("A future complete batch must propose its approval pair")
        if self.proposed_pins["approved"] not in (
            self.pins["approved"],
            batch["pins"],
        ):
            raise ValueError(
                "Proposed approval does not identify the selected candidate"
            )
        if batch.get("previous") not in (None, self.pins["approved"]):
            raise ValueError(
                "Candidate previous pins disagree with the trusted baseline"
            )
        baseline_other = [
            entry for entry in self.pins["batches"] if entry["id"] != batch_id
        ]
        proposed_other = [
            entry for entry in self.proposed_pins["batches"] if entry["id"] != batch_id
        ]
        if baseline_other != proposed_other:
            raise ValueError(
                "A candidate proposal cannot rewrite other rollout records"
            )
        candidate_pins = copy.deepcopy(self.pins)
        candidate_pins["batches"] = [*baseline_other, {**batch, "status": "candidate"}]
        return candidate_pins, batch

    def classify(self):
        """Distinguish candidate changes from recovery states and unrelated edits."""
        changed = [
            batch
            for batch in self.proposed_pins["batches"]
            if batch != self.before.get(batch["id"])
        ]
        subjects = [
            batch
            for batch in changed
            if any(
                batch.get(key) != self.before.get(batch["id"], {}).get(key)
                for key in ("pins", "projects", "previous")
            )
        ]
        if self.proposed_pins["approved"] != self.pins["approved"]:
            subjects = [
                batch
                for batch in changed
                if batch["pins"] == self.proposed_pins["approved"]
            ]
            if not subjects:
                raise ValueError(
                    "Approval change requires a new candidate batch and renewed evidence"
                )
        if not subjects:
            return {
                "classification": "state-only"
                if self.proposed_pins != self.pins
                else "not-applicable",
                "batch": None,
            }
        if len(subjects) != 1:
            raise ValueError("One pin PR must identify one complete candidate batch")
        batch = subjects[0]
        # State-only recovery remains reviewable even when execution is forbidden.
        self._validate_candidate(batch)
        return {"classification": "candidate", "batch": batch["id"]}
