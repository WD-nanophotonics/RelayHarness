# Phase 2 evidence

## Scope

Phase 2 extends the foundation with a durable local mailbox and deterministic handoff ledger. It does not add Mechanics behavior and does not claim a real A/B loop when the required model cannot be launched and verified.

## Gate 1 — deterministic local certification

Passed with the focused command:

```text
python -m unittest tests.test_foundation tests.test_phase2
```

Seven tests passed. They cover atomic payload/message publication, payload and message integrity, exact recipient routing, one-owner claims, immutable messages, task/result records, protected launch authority, short file-based invocations, interrupted handoff recovery, ownership transfer after ACK, and durable supervisory adapter files.

The mailbox layout is:

```text
runs/<run_id>/
  mailbox/
    relay/{pending,claimed,done}/
    worker/{pending,claimed,done}/
  payloads/
  messages/
  claims/
  owners/
```

`MailboxMessage` is small metadata: protocol, message/run/turn identity, sender and exact recipient, kind, payload reference, capsule reference, parent identity, timestamp, payload SHA-256, and message SHA-256. The payload and capsule are durable files. Publication is create-if-absent. Claiming creates one claim record and atomically moves the message from `pending` to `claimed`; completion releases the claim and moves the immutable message to `done`.

`HandoffCoordinator` writes `handoff_pending` before successor launch. `LaunchAuthority` derives executable command, repository working directory, and Luna High request only from the authoritative `ProjectProfile`. Semantic routing may select `relay` or `worker`, but attempts to provide command, shell, cwd, environment, model, reasoning, or runtime-root overrides fail closed. Ownership becomes `owned` only after exact PID/role/capsule ACK and independently supplied model evidence pass.

## Gate 2 — real agent/model path

Not passed. A strictly read-only, ephemeral `codex exec -m Luna` probe reached the provider and returned:

```text
The 'Luna' model is not supported when using Codex with a ChatGPT account.
```

The initial read-only attempt also required elevated local permission for Codex's own state database; the elevated retry was still read-only and failed closed at provider model validation. This is evidence that the current CLI/account combination cannot establish the required Luna High runtime identity.

The earlier browser test remains capability evidence only: the designated ChatGPT conversation accepted an explicitly agent-labeled message and returned `CHATGPT_RELAY_TEST_ACK`. Phase 2 now contains `ChromeDOMTransport`, which isolates browser mechanics behind an injected bridge and durably stores inbound/outbound content. That adapter has not been mislabeled as full RelayHarness end-to-end certification.

## Mechanics gate

Mechanics integration was not permitted because Gate 2 failed. No Mechanics repository was launched, modified, committed, pushed, or otherwise inspected in this phase.

## Acceptance labels

No real-agent PASS labels are emitted. `LUNA_HIGH_CONTROLLER_PASS`, `LUNA_HIGH_WORKER_PASS`, `NO_TERRA_AGENT_PASS`, `NO_SOL_AGENT_PASS`, and `EXPLICIT_MODEL_SELECTION_PASS` remain unsupported until the provider can accept and independently verify Luna High for both roles.

## Next bounded phase

Resolve the provider/model contract for a real Luna High Codex launch, then repeat Gate 2 with one bounded Relay Agent generation. Only after that gate is green should a normal Mechanics profile and one read-only worker inspection be attempted.
