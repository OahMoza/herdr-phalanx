# ADR 0005: Artifact-Led Orchestration

## Status

Accepted. 2026-09-12.

## Context

Herdr Phalanx exposed runtime discovery, direct orchestration, topology, and Agent Bus as one surface. The Bus already had a strong independent implementation boundary, but its queue concepts were unnecessary for the primary workflow. The project needed to:

1. Split into three independently discoverable Skills.
2. Introduce an Artifact/DAG authority model where accepted versioned work products—not raw Task completion—unlock downstream work.
3. Support recursive delegation with finite inherited authority.
4. Demote Agent Bus to optional advanced delivery.

## Decision

Adopt Artifact-led orchestration as the planned default model:

- **Three Skills**: `herdr-runtime-init` (capability inventory), `herdr-phalanx` (default direct orchestration), `herdr-agent-bus` (optional anonymous Worker pools).
- **Artifact authority**: immutable versioned files with SHA-256, producer session, independent review Gate, and explicit accepted-input pinning.
- **Recursive delegation**: one owner per Run, Child Runs created with finite inherited authority, hierarchical budget reservations.
- **Relay/Inbox**: durable cursor-based notifications with at-least-once visibility and bounded wake liveness.
- **Topology persistence**: exclusive Workspace/Tab/Pane allocations with logical release.
- **Lifecycle closure**: Amendments, cooperative cancellation, Red Team findings, Formal Evidence Set, and guarded Run completion.
- **Bus demotion**: Bridge result ingestion moves to Gate-only promotion; Bus remains Worker-only.

Legacy Runs keep `workflow_version=legacy-v1` and current direct completion behavior. New Artifact-led Runs use `artifact-v1`.

## Consequences

- Three Skill entrypoints with non-overlapping descriptions.
- Additive schema migrations preserve existing databases.
- Artifact-led Run creation remains fail-closed until controlled smoke gates pass.
- Bus availability never blocks the direct workflow.
- Documentation, versions, glossary, architecture, and smoke matrix are synchronized.

## Alternatives Considered

- **Keep Bus as default**: rejected—adds queue complexity to the common case.
- **Single Skill with internal modes**: rejected—hides product boundaries and increases trigger conflicts.
- **Direct Task completion without Artifacts**: rejected—lacks evidence reconstruction and independent review.
