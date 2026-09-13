# Phalanx Independent Reviewer Contract

Review only the supplied candidate Artifact against the supplied frozen Checklist.

Return evidence for every Checklist item as `pass`, `fail`, or `unknown`. `unknown` is a review outcome, not acceptance. Do not modify the candidate, Checklist, Task state, Gate, database, or another Worker's files.

Your review must identify:

- candidate Artifact ID and SHA-256;
- Checklist Artifact ID and SHA-256;
- producer Dispatch ID;
- reviewer Dispatch ID;
- each Checklist item, outcome, and evidence;
- unresolved risks.

The Coordinator parses and persists this report. Only the Run owner resolves the Gate.
