# Productization and Agent endpoints

## Product boundary

RelayHarness is an installable runtime and deterministic kernel. It is separate from the semantic Coordinator Agent and Project Worker Agent. A target project invokes RelayHarness from its existing Agent; it does not copy mailbox or lifecycle code into its repository.

## Identity layers

```text
role        = worker | coordinator
endpoint    = long-lived logical target (subprocess or Codex thread/task)
activation  = one bounded RelayHarness generation of that endpoint
```

`AgentEndpoint` is stored in `endpoints/worker.json` or `endpoints/coordinator.json`. `ExecutionIdentity` and `ActivationRecord` are stored under `activations/`. An endpoint may be reused across turns; every activation still has a unique durable ID. Subprocess PID is optional backend evidence, never the universal owner identity.

## Enrollment and binding

`relayharness engage <project> --worker-endpoint-id ...` enrolls an existing Worker endpoint. For a Codex thread, the external thread/task ID is required; window titles and process titles are not accepted as identity. The service creates a run outside target-project semantics and records the Worker as the initial logical owner. A Coordinator endpoint can be bound with `bind-coordinator`; provisioning is a backend operation and is not silently emulated when the host adapter does not expose it.

## Configuration scopes

`InstallationConfig` is machine/product scope: runtime location, Coordinator strategy, reusable provider mappings, and transport defaults. `ProjectProfile` is project scope: repository, branch/policy/safety, and approved Agent backend settings. Logical policy remains `Luna` / `High`; provider IDs such as `gpt-5.6-luna` are backend configuration, not capsule semantics.

## Mailbox versus doorbell

The mailbox carries task/result semantics and nonce payloads. The Codex task/thread backend only sends a short wakeup containing an activation ID and capsule path. Thread history, UI windows, and follow-up prompts are not workflow truth.

Capsule protocol v2 removes the old `next_role` ambiguity: `role` is the current consumer and `successor_role` is future routing after successful completion. A current handoff is valid only when the mailbox recipient, capsule role, endpoint role, execution identity role, and activation ACK role all agree. A v1 capsule with `next_role` is inspect-only and cannot be resumed automatically.

## Actual local Codex capability

The current Codex app exposes local project/thread discovery, existing-thread follow-up, wait, read, and handoff tools. These are available to the Agent as host tools, not as a deterministic Python API. `HostBridgeRequest`/`HostBridgeReceipt` is the explicit fallback boundary: the Kernel generates the exact target and bootstrap-only wakeup, the Agent performs the host call, and the Kernel validates the returned target/activation/turn evidence. The previous CLI probe is not representative of this managed-thread surface: `codex exec -m Luna` was rejected by the provider, while the app tool contract exposes `gpt-5.6-luna` with `high` reasoning. Independent provider evidence is still required before a Luna PASS label.

The bounded probe used an already-existing idle Codex thread as a Coordinator endpoint. The host sent only:

```text
RelayHarness activation activation_gate2_001. Read bootstrap capsule: <absolute capsule path>
```

The thread read the capsule, followed its durable `semantic_ref` to the temporary payload, and returned `ENDPOINT_GATE2_ACK RH_ENDPOINT_GATE2_9f3b`. The nonce did not appear in the wakeup text. This demonstrates endpoint discovery/follow-up/wait/read and wakeup-bootstrap separation. It does not yet demonstrate a second Worker endpoint writing a ResultCapsule and handing back to a new Coordinator activation, so no full `CODEX_ENDPOINT_ROUNDTRIP_PASS` is claimed. The requested `gpt-5.6-luna`/high mapping was accepted by the task-control surface, but independent provider model evidence was not exposed in the returned thread record.

The subsequent two-existing-endpoint certification enrolled the current existing Worker thread and bound the existing idle Coordinator thread. C1 completed a real host turn, wrote and passed a durable `ActivationAck`, claimed the Coordinator mailbox message, and published one Worker task/message. The run stopped before W1 because the real Coordinator emitted a Worker-targeted capsule whose `next_role` was `coordinator`, contradicting the mailbox recipient `worker`; the Kernel preserved the evidence and did not repair it. No full-loop or model PASS is claimed from that partial run. The corrective phase must use a new protocol-v2 run rather than mutating this evidence.
