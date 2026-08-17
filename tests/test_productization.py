from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay_harness.backends import CodexThreadBackend
from relay_harness.config import AgentConfig, InstallationConfig, ProjectProfile, RepositoryConfig, RuntimeConfig
from relay_harness.endpoints import ActivationRecord, AgentEndpoint, EndpointRegistry, ExecutionIdentity
from relay_harness.engagement import EngagementService
from relay_harness.errors import IntegrityError
from relay_harness.host_bridge import ActivationAck, HostBridgeReceipt
from relay_harness.schemas import OwnershipRecord
from relay_harness.storage import read_json


class FakeCodexControl:
    def __init__(self):
        self.sent = []

    def send_follow_up(self, thread_id, prompt, model, thinking):
        self.sent.append((thread_id, prompt, model, thinking))
        return "followup-1"

    def inspect(self, thread_id):
        return {"thread_id": thread_id, "status": "idle"}


class ProductizationTests(unittest.TestCase):
    def profile(self, root: Path) -> ProjectProfile:
        return ProjectProfile(
            project_id="demo",
            repository=RepositoryConfig(str(root), "main"),
            agent=AgentConfig([], "Luna", "High", "codex_thread", "gpt-5.6-luna"),
            runtime=RuntimeConfig(str(root / "runtime")),
        )

    def test_endpoint_activation_and_registry_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            endpoint = AgentEndpoint("worker-endpoint", "codex_thread", "worker", "demo", external_id="thread-worker")
            self.assertEqual(AgentEndpoint.from_dict(endpoint.to_dict()).external_id, "thread-worker")
            registry = EndpointRegistry(root)
            registry.register(endpoint)
            with self.assertRaises(IntegrityError):
                registry.register(AgentEndpoint("other", "codex_thread", "worker", "demo", external_id="thread-other"))
            first = ExecutionIdentity.new("run_1", "turn_1", endpoint)
            second = ExecutionIdentity.new("run_1", "turn_3", endpoint)
            self.assertNotEqual(first.activation_id, second.activation_id)
            registry.record_activation(ActivationRecord(first, "completed"))
            registry.record_activation(ActivationRecord(second, "active"))
            self.assertEqual(registry.latest_activation("worker").identity.activation_id, second.activation_id)

    def test_ownership_is_backend_neutral_and_enrollment_is_external(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = self.profile(root)
            service = EngagementService(profile)
            worker = AgentEndpoint("worker-endpoint", "codex_thread", "worker", "demo", external_id="thread-worker")
            paths, result = service.enroll_current_worker(worker)
            self.assertEqual(result.status, "awaiting_coordinator")
            owner = OwnershipRecord.from_dict(read_json(paths.owners / "enrollment.json"))
            self.assertIsNone(owner.owner_pid)
            self.assertEqual(owner.endpoint_id, "worker-endpoint")
            coordinator = AgentEndpoint("coordinator-endpoint", "codex_thread", "coordinator", "demo", external_id="thread-coordinator")
            ready = service.bind_coordinator(paths, coordinator)
            self.assertEqual(ready.status, "ready")
            summary = service.layout.status_summary()
            self.assertEqual(summary["worker"]["endpoint_id"], "worker-endpoint")
            self.assertEqual(summary["coordinator"]["endpoint_id"], "coordinator-endpoint")
            recovery = service.layout.inspect_recovery(paths.root.name)
            self.assertEqual({item["role"] for item in recovery["endpoints"]}, {"worker", "coordinator"})
            self.assertTrue(recovery["activations"])

    def test_codex_thread_backend_wakeup_is_bootstrap_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = self.profile(Path(temporary))
            control = FakeCodexControl()
            backend = CodexThreadBackend(profile, control, InstallationConfig(provider_models={"Luna": "gpt-5.6-luna"}))
            endpoint = AgentEndpoint("coordinator-endpoint", "codex_thread", "coordinator", "demo", external_id="thread-coordinator")
            identity = ExecutionIdentity.new("run_1", "turn_1", endpoint)
            activation = backend.wake_or_resume(endpoint, identity, ".relayharness/capsules/turn_1_coordinator.json")
            self.assertEqual(activation.backend_evidence["submission_id"], "followup-1")
            self.assertEqual(control.sent[0][2:], ("gpt-5.6-luna", "High"))
            self.assertIn("Read bootstrap capsule:", control.sent[0][1])
            self.assertNotIn("nonce", control.sent[0][1])

    def test_agent_mediated_host_bridge_locks_target_and_activation(self):
        with tempfile.TemporaryDirectory() as temporary:
            profile = self.profile(Path(temporary))
            control = FakeCodexControl()
            backend = CodexThreadBackend(profile, control, InstallationConfig(provider_models={"Luna": "gpt-5.6-luna"}))
            endpoint = AgentEndpoint("coordinator-endpoint", "codex_thread", "coordinator", "demo", external_id="thread-coordinator")
            identity = ExecutionIdentity.new("run_1", "turn_1", endpoint)
            request = backend.build_host_bridge_request(endpoint, identity, ".relayharness/capsules/turn_1_coordinator.json")
            self.assertEqual(request.thread_id, "thread-coordinator")
            self.assertIn("Read bootstrap capsule:", request.wakeup_text)
            with self.assertRaises(IntegrityError):
                HostBridgeReceipt(
                    request.request_id, request.operation, request.endpoint_id, "other-thread", request.activation_id,
                    request.wakeup_text, True, "submission-1", {"thread_id": "other-thread"},
                ).validate_for(request)
            with self.assertRaises(IntegrityError):
                HostBridgeReceipt(
                    request.request_id, request.operation, request.endpoint_id, request.thread_id, request.activation_id,
                    "arbitrary semantic task", True, "submission-1", {"thread_id": request.thread_id},
                ).validate_for(request)
            receipt = HostBridgeReceipt(
                request.request_id, request.operation, request.endpoint_id, request.thread_id, request.activation_id,
                request.wakeup_text, True, "submission-1", {"thread_id": request.thread_id, "submission_id": "submission-1", "accepted": True},
            )
            activation = backend.accept_host_bridge_receipt(endpoint, identity, request, receipt)
            self.assertEqual(activation.backend_evidence["submission_id"], "submission-1")

    def test_coordinator_mailbox_role_is_backward_compatible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = self.profile(root)
            service = EngagementService(profile)
            worker = AgentEndpoint("worker-endpoint", "codex_thread", "worker", "demo", external_id="thread-worker")
            paths, _ = service.enroll_current_worker(worker)
            coordinator = AgentEndpoint("coordinator-endpoint", "codex_thread", "coordinator", "demo", external_id="thread-coordinator")
            service.bind_coordinator(paths, coordinator)
            mailbox = service.layout.mailbox(paths)
            self.assertTrue((paths.mailbox / "coordinator" / "pending").is_dir())
            payload_ref, digest = mailbox.publish_payload("coordinator-note.md", b"mail")
            capsule = paths.capsules / "turn_1_coordinator.json"
            capsule.write_text("{}", encoding="utf-8")
            from relay_harness.schemas import MailboxMessage
            message = MailboxMessage(
                "coordinator-message", paths.root.name, "turn_1", "worker", "coordinator", "result",
                payload_ref, "capsules/turn_1_coordinator.json", digest,
            )
            mailbox.publish_message(message)
            self.assertEqual(mailbox.state("coordinator-message", "coordinator"), "pending")

    def test_activation_ack_requires_exact_capsule_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            capsule = Path(temporary) / "capsule.json"
            capsule.write_text('{"kind":"bootstrap"}\n', encoding="utf-8")
            from relay_harness.storage import sha256_file
            ack = ActivationAck("a1", "e1", "worker", "run1", "turn1", "c1", "capsule.json", sha256_file(capsule))
            ack.validate_against({
                "activation_id": "a1", "endpoint_id": "e1", "role": "worker", "run_id": "run1",
                "turn_id": "turn1", "capsule_id": "c1", "capsule_ref": "capsule.json",
            }, capsule)
            with self.assertRaises(IntegrityError):
                ActivationAck("a2", "e1", "worker", "run1", "turn1", "c1", "capsule.json", sha256_file(capsule)).validate_against({
                    "activation_id": "a1", "endpoint_id": "e1", "role": "worker", "run_id": "run1",
                    "turn_id": "turn1", "capsule_id": "c1", "capsule_ref": "capsule.json",
                }, capsule)


if __name__ == "__main__":
    unittest.main()
