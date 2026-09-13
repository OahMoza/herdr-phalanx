# Artifact/DAG Workflow

This is the planned `artifact-v1` authority model. Public Artifact-led Run creation remains fail-closed until the direct workflow smoke gate lands.

## Authority

- Artifact bodies are immutable files below `PHALANX_ARTIFACTS` (default `~/.herdr-phalanx/artifacts`).
- Phalanx DB stores the storage key, SHA-256, logical name, version, producer, status, supersession, and Gate linkage.
- Only the owning Coordinator invokes Artifact registration or promotion commands.
- Workers submit a candidate source reference; they do not write directly into the authoritative Artifact root.

## Candidate Registration

A producing Task declares a logical output slot. Register a candidate with:

```text
python db/phalanx_db.py artifact-register \
  --run <run-id> --coordinator <owner> --task <task-id> --dispatch <dispatch-id> \
  --logical-name <name> --type <type> --source <file> --idempotency-key <key>
```

Registration copies the source to a temporary sibling, hashes both source and copy, atomically renames the copy, and commits metadata and an Event. Missing, empty, undeclared/conflicting, escaping, or hash-mismatched inputs fail without acceptance.

A retry with the same Run/idempotency key returns the same Artifact when its logical output and type match. A correction uses a new idempotency key and may name `--supersedes <artifact-id>`.

## Review and Promotion

A candidate receives a distinct Gate. For Artifact-led Runs, Gate create/resolve requires the stored Coordinator identity. The reviewer must be a different Dispatch with a recorded Herdr session distinct from the producer session.

- `pass`: candidate becomes accepted; its logical output slot points to that exact Artifact ID.
- `fail`: candidate becomes rejected; correction creates another immutable version and Gate.
- `escalate`: Gate records escalation; it does not imply failure or acceptance.
- A terminal Gate cannot be resolved again.

Accepting a correction marks the named earlier accepted Artifact `superseded`; old content remains readable. Existing downstream pins do not change automatically.

## Dependency Pinning

The Constitution names future logical output slots. After upstream acceptance, an owner-authorized transition pins one exact accepted Artifact ID before downstream claim. The current U3 storage layer records slots and accepted versions; U4 adds readiness and input-pin transitions.

## Recovery

Artifact files and SQLite do not share a transaction. A future reconciliation command will:

- adopt an orphan only when storage key, idempotency key, and hash all match;
- otherwise quarantine it outside the authoritative name and permit a clean retry;
- block on an accepted row whose body is missing or hash-mismatched.

Back up and restore the Phalanx DB and Artifact root as one coordinated evidence set.
