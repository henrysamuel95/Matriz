# Codex Desktop loses all local projects and task assignments after restart

## Environment

- Codex Desktop version: `26.903.8094.0`
- OS: Windows (`10.0.26100`, x64)
- Date reproduced: 2026-09-09 (America/Bogota)
- Affected host: local

## Summary

Codex Desktop successfully allows local projects to be restored and existing tasks to be assigned to them, but closing and reopening the application erases the project registry and every task-to-project assignment. This has happened more than once after the recent application update.

The problem is not limited to the sidebar presentation and is not caused by Git. After startup, the persisted global-state file itself has been rewritten with zero local projects and zero thread assignments. Both Git and non-Git project folders are affected.

## Projects affected

- Matriz
- Tos
- monitoreo_central
- Software_monitoreo
- Codex
- SGO

## Reproduction steps

1. Restore/create the six local projects listed above.
2. Reassign the existing Codex tasks to their projects.
3. Confirm that six projects and 62 task assignments are visible and persisted.
4. Fully close Codex Desktop.
5. Reopen Codex Desktop.

## Expected result

All projects, sidebar organization, and task-to-project assignments persist across application restarts.

## Actual result

- Local projects after restart: `0`
- Task-to-project assignments after restart: `0`
- Projects section order after restart: empty
- Every listed Codex task has `projectId: null`
- The migration marker remains:

```json
{
  "version": 1,
  "projectsMigrated": true,
  "threadAssignmentsMigrated": false
}
```

This combination suggests that startup migration/state reconciliation considers the project migration complete while failing to migrate thread assignments, then overwrites the valid restored state with an empty project registry.

## Evidence

- Valid pre-restart recovery snapshot: 5 persisted projects, 6 direct assignment records, and a populated project order.
- Current state after restart: 0 projects, 0 assignments, and no project order.
- Current state SHA-256: `4c738a09ebfb1cda19608dbfb5b74e93e1cb7ff63748d227a5205530e121a4e3`
- Pre-restart recovery snapshot SHA-256: `000d204bacc3dfc2e554bcc8b3870c66ad52dd38bf1cec9ac0a847c191a8864b`
- The application's automatic `.bak` now matches the empty current state, so it does not preserve the last known-good configuration.

## Impact

The user's working organization is lost on every restart. Restoring projects and reassigning dozens of tasks is temporary because the next launch deletes the restored configuration again.

## Requested fix

Please prevent startup migration/state reconciliation from replacing a valid local project registry and its task assignments with empty state. If migration is incomplete, preserve the last known-good state and create a recoverable backup before any rewrite.
