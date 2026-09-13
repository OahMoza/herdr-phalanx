# Phalanx Coordinator Capability Smoke

This is a non-destructive smoke contract that proves an Agent/Profile can own a Run, manage Delegations and budgets, handle Inbox, and perform acceptance. It must not derive from Worker roles.

## Contract

The smoke must prove:

1. **Owner-scoped mutation rejection**: a different Coordinator cannot mutate the Run.
2. **One Wake Cycle**: claim a Task, dispatch, settle, and observe readiness.
3. **Inbox handling**: post, list, and acknowledge a notification.
4. **Finite budget enforcement**: a Child Delegation cannot overcommit the parent budget.
5. **Simulated Child result acceptance**: submit a Delegation Result and accept it through a Gate.

## Usage

```text
python db/phalanx_db.py coordinator-smoke --coordinator <name> --kind <kind> --profile <profile>
```

The command records a `coordinator` capability observation. Worker verification alone never qualifies.
