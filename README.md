# RelayHarness

RelayHarness is an independent, installable local runtime for enrolling existing project Agents into durable, recoverable, file-backed multi-Agent workflows. RelayHarness is the product and deterministic Kernel; it is not the Coordinator Agent.

This repository is the generic foundation only. It contains no Mechanics, GenericChess, Gmail, Chrome, browser automation, or project-specific engineering logic.

## The boundary

The deterministic Kernel owns structure: runtime directories, identities, manifests, atomic writes, hashes, claims, process identity, model-policy checks, journal entries, incident evidence, and recovery inspection. It does not decide engineering work.

The Coordinator Agent is a semantic endpoint managed by RelayHarness. It interprets supervisory input and worker results, chooses bounded next work, and makes semantic CONTINUE / COMPLETE / HUMAN_REQUIRED decisions. The Project Worker Agent is another semantic endpoint. Existing project Agents can enroll as Workers; target repositories normally need only a small profile, not RelayHarness source copies.

Project Agent B will eventually be a real project-specific AI process. It will perform semantic work inside a configured repository and produce a durable result. The Kernel will own launch, ACK, liveness, ownership transfer, and terminal capture; B will never need to remember how to keep the workflow alive.

The rule is simple: hard mechanism belongs to Python, semantic decisions belong to Agents, and continuity belongs to durable files. Roles, endpoints, and activations are distinct: one long-lived Codex thread endpoint may have many bounded RelayHarness activations.

```text
Existing project Agent --engage--> RelayHarness runtime
                                      |
                       Coordinator endpoint ↔ Worker endpoint
                                      |
                         deterministic mailbox + Kernel
```

## Project profiles

`relayharness init <project>` creates a generic JSON profile under `.relayharness/projects/`. The profile contains:

- project identity;
- repository path and optional branch;
- agent command plus the required explicit `Luna` / `High` model request;
- runtime root;
- supervisory transport adapter name and optional external ID;
- bounded/continuous policy, safe-stop behavior, turn limits, Git policy, push policy, and safety constraints.

See [`docs/project-profile.example.json`](docs/project-profile.example.json). No current repository path or domain assumption is embedded in the schema.

Machine/product configuration is separate from the small project profile. `InstallationConfig` can hold reusable runtime location, coordinator backend strategy, provider model mappings such as logical `Luna` → provider `gpt-5.6-luna`, and transport defaults. Project profiles hold repository identity, policy, safety constraints, and endpoint/backend choices.

## Durable runtime

The default runtime is file-backed and conceptually shaped as:

```text
.relayharness/
  projects/<project>.json
  state/<project>/
    objective.md
    constraints.json
    decisions.jsonl
    unresolved.json
  runs/<run_id>/
    run.json
    mailbox/{relay,worker}/{pending,claimed,done}/
    messages/
    payloads/
    capsules/
    tasks/
    results/
    claims/
    owners/
    logs/journal.jsonl
    incidents/<incident_id>/
    terminal/
```

Large semantic content lives in referenced files. A protocol-v2 Bootstrap/Continuation Capsule carries only identity, model requirement, profile/state references, the current semantic delta reference, repository/Git expectations, output/logging contracts, the current executing `role`, and the post-completion `successor_role`/terminal contract. It does not recursively copy prompts. Historical protocol-v1 capsules with ambiguous `next_role` are inspect-only and cannot be silently resumed.

Phase 2 adds a small mailbox: immutable structured messages are published to the exact recipient's `pending` directory, claimed by one owner, and moved to `done` only after completion. Payloads are hashed files; message metadata carries the payload hash and its own integrity hash. A handoff ledger records `handoff_pending` before launch and changes to `owned` only after the successor's exact PID/role/capsule ACK and independently supplied model evidence are accepted.

After a process dies, `relayharness recover <project> <run_id>` inspects the durable manifest, capsules, tasks, results, and claims and reports whether a structurally complete continuation exists. Inspection is deliberately not semantic recovery certification and does not launch an agent.

## Lifecycle and observability

Claims are create-if-absent JSON records, so ownership cannot silently be overwritten. Atomic replacement plus fsync protects individual durable writes. The normal journal is small JSONL containing mechanical facts such as PID, role, model, hashes, ACKs, ownership transfers, and terminal state. Incident bundles are created only for suspicious or exceptional conditions and separate an agent's structured concern from objective evidence.

Elapsed time alone is not an incident. Future watchdog/monitoring code must combine liveness and meaningful activity evidence with elapsed time.

## Model policy

Every future real agent launch must request exactly `Luna` with `High` reasoning and must provide independently verifiable model evidence. `ModelPolicy` rejects Terra, Sol, aliases, and omitted/default selection. The launcher boundary refuses a launch without an explicit `ModelRequest`; provider-specific argument encoding and live verification belong to the next integration phase. This foundation does not claim `LUNA_HIGH_CONTROLLER_PASS` or worker certification because no real agent was launched.

The Phase 2 probe demonstrated that the installed Codex CLI currently rejects `Luna` for the ChatGPT account (`model not supported`), so no real A/B process or Mechanics pilot is certified. The implementation fails closed rather than falling back to another model.

## Supervisory transport

The Kernel depends only on `SupervisoryTransport` (`read_latest`, `submit`, `verify`). The included `ManualTransport` is intentionally non-networked. Future adapters can target a chat or another external system without coupling the local runtime to ChatGPT, Chrome, or Gmail. A transport outage can be represented durably as `waiting_for_external_audit`.

## CLI

```text
relayharness init <project>
relayharness engage <project> --worker-endpoint-id <id> [--worker-external-id <thread>]
relayharness bind-coordinator <project> <run_id> --coordinator-endpoint-id <id> --coordinator-external-id <thread>
relayharness start <project>
relayharness stop <project> <run_id>
relayharness status <project>
relayharness recover <project> <run_id>
relayharness inspect <project> <run_id>
relayharness doctor <project>
```

`start` creates a durable run; it does not pretend to be Relay Agent A or launch project work in this foundation phase.

`engage` enrolls the already-existing Worker endpoint and creates an external run. It does not infer Agent identity from a window title or PID. `status` summarizes coordinator/worker endpoints, current owner, run state, and next expected role rather than dumping the journal.

The filesystem mailbox is the semantic channel. A Codex thread/task backend is only a doorbell: its wakeup text is a short activation ID plus a bootstrap capsule path. It must not carry the task nonce or a large prompt.

The managed Codex task surface has been capability-tested with an existing idle thread: it received only a bootstrap path, read the durable payload, and returned the payload nonce acknowledgement. This is endpoint wakeup evidence, not a full two-endpoint mailbox round-trip; the latter remains a bounded next step after the product API is hosted by a real Agent.

## Principles

1. AI never waits; software waits when waiting is required.
2. AI does not remember the workflow; software enforces it.
3. Preserve behavior contracts, not historical implementation.
4. No ownerless continuation.
5. No lifecycle correctness based only on prompt obedience.
6. Large semantic content lives in files; process arguments carry small identity/location metadata.
7. Every transition must be reconstructable from durable evidence.
8. Structure is deterministic; intelligence remains flexible.
9. Normal logs are cheap; incident evidence is rich.
10. Project-specific semantics stay outside the generic core.

## Development

The implementation uses only the Python standard library. Run the narrow foundation certification with:

```text
python -m unittest tests.test_foundation
python -m compileall relay_harness tests
git diff --check
```

The next bounded phase may attach one real project, likely Mechanics, as Project Agent B. It is intentionally not implemented here.
