from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay_harness.config import AgentConfig, ProjectProfile, RepositoryConfig, RuntimeConfig
from relay_harness.errors import IntegrityError, SchemaError
from relay_harness.endpoints import AgentEndpoint, ExecutionIdentity
from relay_harness.handoff import HandoffCoordinator, validate_current_target, validate_successor_routing
from relay_harness.mailbox import MailboxStore
from relay_harness.model_policy import ModelEvidence
from relay_harness.process import AgentLauncher, LaunchAuthority, ProcessHandle, StartupAck
from relay_harness.runtime import RuntimeLayout
from relay_harness.schemas import BootstrapCapsule, MailboxMessage, ResultCapsule, TaskCapsule, default_terminal_contract
from relay_harness.storage import atomic_write_bytes, atomic_write_json
from relay_harness.transport import ChromeDOMTransport


class RecordingLauncher(AgentLauncher):
    def __init__(self):
        self.specs = []

    def launch(self, spec):
        self.specs.append(spec)
        return ProcessHandle(4242, object(), "2026-01-01T00:00:00+00:00")


class FakeBridge:
    def __init__(self):
        self.submitted = []

    def read_latest(self):
        return "chat-1", "external supervisory instruction"

    def submit(self, body):
        self.submitted.append(body)
        return "submission-1"

    def verify(self, submission_id):
        return submission_id == "submission-1"


class Phase2Tests(unittest.TestCase):
    def make_profile(self, root: Path) -> ProjectProfile:
        return ProjectProfile(
            project_id="demo",
            repository=RepositoryConfig(str(root), "main"),
            agent=AgentConfig(["agent-wrapper", "--capsule"], "Luna", "High"),
            runtime=RuntimeConfig(str(root / "runtime")),
        )

    def make_capsule(self, paths, role="worker", successor_role="coordinator") -> tuple[BootstrapCapsule, Path]:
        capsule = BootstrapCapsule(
            project_id="demo", run_id=paths.root.name, turn_id="turn_1", role=role,
            runtime_root=str(paths.root), profile_ref="profiles/demo.json", durable_state_refs={"objective": "state/objective.md"},
            semantic_ref="payloads/task.md", repository={"path": "repo", "branch": "main"},
            expected_git={"head": "abc", "branch": "main"}, output_contract={"result": "results/*.json"},
            logging_contract={"journal": "logs/journal.jsonl"}, successor_role=successor_role, terminal_contract=default_terminal_contract(),
        )
        path = paths.capsules / f"turn_1_{role}.json"
        from relay_harness.storage import atomic_write_json
        atomic_write_json(path, capsule.to_dict())
        return capsule, path

    def make_message(self, mailbox: MailboxStore, paths, capsule_path: Path, recipient="worker"):
        payload_ref, digest = mailbox.publish_payload("task.md", b"read-only task")
        return MailboxMessage(
            message_id="message_1", run_id=paths.root.name, turn_id="turn_1", sender_role="relay",
            recipient_role=recipient, message_kind="task", payload_ref=payload_ref,
            capsule_ref=str(capsule_path.relative_to(paths.root)).replace("\\", "/"), payload_sha256=digest,
        )

    def test_mailbox_publication_integrity_claim_and_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout = RuntimeLayout(self.make_profile(root))
            paths = layout.new_run()
            mailbox = layout.mailbox(paths)
            _, capsule_path = self.make_capsule(paths)
            message = self.make_message(mailbox, paths, capsule_path)
            mailbox.publish_message(message)
            self.assertEqual(mailbox.state("message_1", "worker"), "pending")
            with self.assertRaises(FileNotFoundError):
                mailbox.claim("message_1", "relay", 42, "turn_1")
            with self.assertRaises(IntegrityError):
                mailbox.publish_message(MailboxMessage(
                    message_id="message_1", run_id=paths.root.name, turn_id="turn_1", sender_role="relay",
                    recipient_role="worker", message_kind="task", payload_ref=message.payload_ref,
                    capsule_ref=message.capsule_ref, payload_sha256="0" * 64,
                ))
            mailbox.claim("message_1", "worker", 42, "turn_1")
            with self.assertRaises(IntegrityError):
                mailbox.claim("message_1", "worker", 43, "turn_1")
            mailbox.complete("message_1", "worker", 42)
            self.assertEqual(mailbox.state("message_1", "worker"), "done")
            done_path = paths.mailbox / "worker" / "done" / "message_1.json"
            tampered = message.to_dict()
            tampered["message_kind"] = "result"
            atomic_write_json(done_path, tampered)
            with self.assertRaises(SchemaError):
                mailbox.load("message_1", "worker")

    def test_handoff_is_recoverable_and_launch_authority_is_protected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = self.make_profile(root)
            layout = RuntimeLayout(profile)
            paths = layout.new_run()
            mailbox = layout.mailbox(paths)
            capsule, capsule_path = self.make_capsule(paths)
            message = self.make_message(mailbox, paths, capsule_path)
            mailbox.publish_message(message)
            launcher = RecordingLauncher()
            coordinator = HandoffCoordinator(layout, launcher)
            plan = coordinator.prepare(paths, message, capsule, "relay", 7)
            self.assertEqual(plan.recipient_role, "worker")
            report = layout.inspect_recovery(paths.root.name)
            self.assertEqual(report["next_deterministic_action"], "launch_successor:worker:message_1")
            with self.assertRaises(IntegrityError):
                LaunchAuthority(profile).build_spec(capsule, capsule_path, {"command": ["pwsh", "-c", "bad"]})

            handle = launcher.launch(plan.launch_spec)
            ack = StartupAck(handle.pid, "worker", str(capsule_path), "2026-01-01T00:00:01+00:00")
            owner = coordinator.commit_after_ack(paths, plan, capsule, handle, ack, ModelEvidence("Luna", "High", "provider"))
            self.assertTrue(owner.exists())
            self.assertEqual(layout.inspect_recovery(paths.root.name)["next_deterministic_action"], "continue_owner:worker:message_1")
            self.assertEqual(launcher.specs[0].command, tuple(profile.agent.command))
            self.assertEqual(launcher.specs[0].capsule_path, capsule_path)

    def test_current_recipient_and_successor_role_invariants(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout = RuntimeLayout(self.make_profile(root))
            paths = layout.new_run()
            mailbox = layout.mailbox(paths)
            capsule, capsule_path = self.make_capsule(paths, role="worker", successor_role="coordinator")
            message = self.make_message(mailbox, paths, capsule_path, recipient="worker").seal()
            validate_current_target(message, capsule)
            endpoint = AgentEndpoint("worker-endpoint", "codex_thread", "worker", "demo", external_id="thread-w")
            identity = ExecutionIdentity.new(paths.root.name, "turn_1", endpoint)
            validate_current_target(message, capsule, endpoint, identity)
            validate_successor_routing(capsule, "coordinator")
            coordinator_capsule, coordinator_path = self.make_capsule(paths, role="coordinator", successor_role="worker")
            coordinator_message = self.make_message(mailbox, paths, coordinator_path, recipient="coordinator").seal()
            validate_current_target(coordinator_message, coordinator_capsule)
            with self.assertRaises(IntegrityError):
                validate_current_target(message, BootstrapCapsule(
                    project_id="demo", run_id=paths.root.name, turn_id="turn_1", role="coordinator",
                    runtime_root=str(paths.root), profile_ref="profiles/demo.json", durable_state_refs={}, semantic_ref=None,
                    repository={"path": "repo", "branch": "main"}, expected_git={}, output_contract={"result": "x"},
                    logging_contract={"journal": "x"}, successor_role="worker", terminal_contract=default_terminal_contract(),
                ))
            with self.assertRaises(IntegrityError):
                validate_successor_routing(capsule, "worker")
            with self.assertRaises(IntegrityError):
                validate_current_target(message, capsule, AgentEndpoint("coordinator-endpoint", "codex_thread", "coordinator", "demo", external_id="thread-c"), identity)

    def test_terminal_successor_is_explicit_and_ack_target_is_current_role(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout = RuntimeLayout(self.make_profile(root))
            paths = layout.new_run()
            capsule, _ = self.make_capsule(paths, role="coordinator", successor_role=None)
            validate_successor_routing(capsule, None)
            endpoint = AgentEndpoint("coordinator-endpoint", "codex_thread", "coordinator", "demo", external_id="thread-c")
            identity = ExecutionIdentity.new(paths.root.name, "turn_1", endpoint)
            from relay_harness.schemas import MailboxMessage
            message = MailboxMessage("m", paths.root.name, "turn_1", "worker", "coordinator", "result", "payloads/x", "capsules/x", "0" * 64).seal()
            with self.assertRaises(IntegrityError):
                validate_current_target(message, capsule, endpoint, ExecutionIdentity.new(paths.root.name, "turn_1", AgentEndpoint("worker-endpoint", "codex_thread", "worker", "demo", external_id="thread-w")))

            layout.write_bootstrap(paths, capsule)
            report = layout.inspect_recovery(paths.root.name)
            self.assertTrue(report["continuation_structurally_complete"])

    def test_task_result_roundtrip_and_external_transport_are_durable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layout = RuntimeLayout(self.make_profile(root))
            paths = layout.new_run()
            task = TaskCapsule("demo", paths.root.name, "turn_1", "relay", "payloads/task.md", None)
            result = ResultCapsule("demo", paths.root.name, "turn_1", task.task_id, "worker", "completed", "payloads/result.md")
            layout.write_task(paths, task)
            layout.write_result(paths, result)
            transport = ChromeDOMTransport(root / "transport", FakeBridge())
            inbound = transport.read_latest()
            self.assertTrue(inbound and Path(inbound.body_ref).exists())
            payload = root / "transport" / "out.md"
            atomic_write_bytes(payload, b"agent response")
            submission = transport.submit(str(payload))
            self.assertEqual(submission, "submission-1")
            self.assertTrue(transport.verify(submission))


if __name__ == "__main__":
    unittest.main()
