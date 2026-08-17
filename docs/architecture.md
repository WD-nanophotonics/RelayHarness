# RelayHarness product architecture

RelayHarness is the independent runtime/product. The Coordinator Agent and Project Worker Agent are semantic endpoints managed by it. RelayHarness is not a synonym for Agent A.

## Ownership model

The Kernel is the only component allowed to implement lifecycle mechanics. A and B are semantic roles with process contracts. A task capsule is a durable handoff from A to B; a result capsule is a durable handoff from B to A. A claim file records the owner of a resource. A successor can be launched only from a durable capsule and its startup ACK must match the PID launched by the Kernel.

The universal identity is now `AgentEndpoint` plus `ExecutionIdentity`, not PID. An endpoint may be a subprocess or a Codex thread/task. An activation is one bounded run/turn of that endpoint and remains distinct even when the same thread or process identity is reused. PID is preserved as subprocess-specific evidence.

The foundation deliberately has no Controller class. A future Relay Agent A implementation can be replaced without changing the Kernel's storage, capsule, or transport contracts.

## Capsule fields

Every bootstrap capsule carries protocol, project/run/turn/role identity, required Luna High selection, runtime and profile references, durable state references, an optional current semantic reference, repository and expected Git metadata, output and logging contracts, the next role, and terminal behavior. The current task or result is a reference, not a prompt-history copy.

Task and result capsules carry their own run/turn identity and references to larger semantic files. Their validation is structural; the Kernel does not judge whether an engineering result is good.

## Recovery

`RuntimeLayout.inspect_recovery` is intentionally conservative. It reconstructs what files exist, parses every capsule, reports malformed evidence, and identifies whether a continuation is structurally plausible. It does not infer what A should think, retry a failed process, or certify a successful crash-recovery run. Those are future bounded phases with explicit tests.

Phase 2 adds a mailbox under each run. Messages are immutable small records addressed to exactly `relay` or `worker`; semantic payloads are separate hashed files. A message moves from `pending` to `claimed` to `done`, with one claim record and an ownership ledger. An interrupted `handoff_pending` record tells recovery which exact successor role and message must be resumed.

## Process contract

`SubprocessLauncher` requires an explicit `ModelRequest`, passes the capsule location and exact model request as launch metadata, and returns the exact child PID. `StartupAck` and `LivenessEvidence` are typed records; `verify_startup_ack` rejects a PID mismatch. Provider-specific model flag encoding and independent runtime model evidence must be supplied by a future real-agent adapter.

`LaunchAuthority` derives command, repository working directory, and Luna High selection from the authoritative profile. Semantic routing is limited to the next role; command, shell, cwd, environment, model, reasoning, and runtime-root overrides are rejected.

`AgentBackend` is the backend boundary. `SubprocessBackend` preserves the tested PID launcher. `CodexThreadBackend` uses an injected host control surface for bind, inspect, and short follow-up wakeups. The current local Codex app exposes list/read/send/wait operations for threads; the Python runtime does not guess or embed MCP calls, so a host bridge must be supplied. A wakeup looks like `RelayHarness activation <id>. Read bootstrap capsule: <path>`.

## Incident evidence

The journal is append-only JSONL for every run. Incidents have a small concern record plus separately preserved objective evidence. This prevents subjective agent prose from being the only basis for escalation and keeps normal supervisory traffic small.

`ChromeDOMTransport` is optional and sits outside the Kernel. It receives an injected browser bridge, writes inbound external content to local files before returning it, and durably records outbound submissions and verification. The Kernel depends only on `SupervisoryTransport`, never on DOM selectors or Chrome state.

## Deliberate exclusions

No historical AgentRelay/GmailCourier state machine, Gmail polling, Gmail watchdog, old Supervisor, persistent Runner, DRAINING state, transport reconciliation, legacy compatibility layer, browser orchestration, ChatGPT-specific protocol, or certification workaround is part of the core.

## Product invocation

`relayharness engage` registers an authoritative current Worker endpoint supplied by the invoking Agent/platform, creates a durable run, and records enrollment without modifying the target repository. A Coordinator can be bound later with `bind-coordinator`, or a future installation backend may provision one. `status` is a concise operator surface; endpoint registry and activation records remain the durable recovery evidence.
