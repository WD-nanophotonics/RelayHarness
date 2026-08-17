# RelayHarness

RelayHarness is a reusable local runtime for connecting a supervisory AI conversation to arbitrary project-specific AI agents through durable, recoverable, file-backed handoffs.

This repository is the generic foundation only. It contains no Mechanics, GenericChess, Gmail, Chrome, browser automation, or project-specific engineering logic.

## The boundary

The deterministic Kernel owns structure: runtime directories, identities, manifests, atomic writes, hashes, claims, process identity, model-policy checks, journal entries, incident evidence, and recovery inspection. It does not decide engineering work.

Relay Agent A will eventually be a real AI process. It will interpret supervisory input and worker results, choose a bounded next task, and make semantic CONTINUE / COMPLETE / HUMAN_REQUIRED decisions. This phase defines its durable execution contract but does not fake its intelligence with a Python checklist.

Project Agent B will eventually be a real project-specific AI process. It will perform semantic work inside a configured repository and produce a durable result. The Kernel will own launch, ACK, liveness, ownership transfer, and terminal capture; B will never need to remember how to keep the workflow alive.

The rule is simple: hard mechanism belongs to Python, semantic decisions belong to agents, and continuity belongs to durable files.

## Project profiles

`relayharness init <project>` creates a generic JSON profile under `.relayharness/projects/`. The profile contains:

- project identity;
- repository path and optional branch;
- agent command plus the required explicit `Luna` / `High` model request;
- runtime root;
- supervisory transport adapter name and optional external ID;
- bounded/continuous policy, safe-stop behavior, turn limits, Git policy, push policy, and safety constraints.

See [`docs/project-profile.example.json`](docs/project-profile.example.json). No current repository path or domain assumption is embedded in the schema.

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
    capsules/
    tasks/
    results/
    claims/
    owners/
    logs/journal.jsonl
    incidents/<incident_id>/
    terminal/
```

Large semantic content lives in referenced files. A Bootstrap/Continuation Capsule carries only identity, protocol version, model requirement, profile/state references, the current semantic delta reference, repository/Git expectations, output/logging contracts, and the next-role/terminal contract. It does not recursively copy prompts.

After a process dies, `relayharness recover <project> <run_id>` inspects the durable manifest, capsules, tasks, results, and claims and reports whether a structurally complete continuation exists. Inspection is deliberately not semantic recovery certification and does not launch an agent.

## Lifecycle and observability

Claims are create-if-absent JSON records, so ownership cannot silently be overwritten. Atomic replacement plus fsync protects individual durable writes. The normal journal is small JSONL containing mechanical facts such as PID, role, model, hashes, ACKs, ownership transfers, and terminal state. Incident bundles are created only for suspicious or exceptional conditions and separate an agent's structured concern from objective evidence.

Elapsed time alone is not an incident. Future watchdog/monitoring code must combine liveness and meaningful activity evidence with elapsed time.

## Model policy

Every future real agent launch must request exactly `Luna` with `High` reasoning and must provide independently verifiable model evidence. `ModelPolicy` rejects Terra, Sol, aliases, and omitted/default selection. The launcher boundary refuses a launch without an explicit `ModelRequest`; provider-specific argument encoding and live verification belong to the next integration phase. This foundation does not claim `LUNA_HIGH_CONTROLLER_PASS` or worker certification because no real agent was launched.

## Supervisory transport

The Kernel depends only on `SupervisoryTransport` (`read_latest`, `submit`, `verify`). The included `ManualTransport` is intentionally non-networked. Future adapters can target a chat or another external system without coupling the local runtime to ChatGPT, Chrome, or Gmail. A transport outage can be represented durably as `waiting_for_external_audit`.

## CLI

```text
relayharness init <project>
relayharness start <project>
relayharness stop <project> <run_id>
relayharness status <project>
relayharness recover <project> <run_id>
relayharness inspect <project> <run_id>
relayharness doctor <project>
```

`start` creates a durable run; it does not pretend to be Relay Agent A or launch project work in this foundation phase.

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
