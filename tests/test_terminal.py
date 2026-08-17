from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from relay_harness.config import AgentConfig, ProjectProfile, RepositoryConfig, RuntimeConfig
from relay_harness.endpoints import ActivationRecord, AgentEndpoint, ExecutionIdentity
from relay_harness.errors import IntegrityError, SchemaError
from relay_harness.engagement import EngagementService
from relay_harness.runtime import RuntimeLayout
from relay_harness.schemas import BootstrapCapsule, MailboxMessage, TerminalDecision, default_terminal_contract
from relay_harness.storage import atomic_write_bytes, atomic_write_json


class TerminalCertificationTests(unittest.TestCase):
    def profile(self, root: Path) -> ProjectProfile:
        return ProjectProfile(
            project_id="demo",
            repository=RepositoryConfig(str(root), "main"),
            agent=AgentConfig([], "Luna", "High", "codex_thread", "gpt-5.6-luna"),
            runtime=RuntimeConfig(str(root / "runtime")),
        )

    def setup_run(self, root: Path, successor_role: str | None = None):
        profile = self.profile(root)
        service = EngagementService(profile)
        worker = AgentEndpoint("worker-endpoint", "codex_thread", "worker", "demo", external_id="worker-thread")
        paths, _ = service.enroll_current_worker(worker)
        coordinator = AgentEndpoint("coordinator-endpoint", "codex_thread", "coordinator", "demo", external_id="coordinator-thread")
        service.bind_coordinator(paths, coordinator)
        turn_id = "turn_3_coordinator"
        capsule = BootstrapCapsule(
            project_id="demo", run_id=paths.root.name, turn_id=turn_id, role="coordinator",
            runtime_root=str(paths.root), profile_ref="profiles/demo.json", durable_state_refs={}, semantic_ref=None,
            repository={"path": str(root), "branch": "main"}, expected_git={}, output_contract={"result": "results/*.json"},
            logging_contract={"journal": "logs/journal.jsonl"}, successor_role=successor_role,
            terminal_contract=default_terminal_contract(),
        )
        capsule_path = service.layout.write_bootstrap(paths, capsule)
        identity = ExecutionIdentity.new(paths.root.name, turn_id, coordinator)
        service.registry.record_activation(ActivationRecord(identity, "active", ack_verified=True))
        manifest = atomic_read_manifest(paths.manifest)
        manifest["current_turn_id"] = turn_id
        atomic_write_json(paths.manifest, manifest)
        return profile, service.layout, paths, capsule, coordinator, identity, capsule_path

    def valid_decision(self, paths, identity, endpoint_id="coordinator-endpoint", result_refs=None):
        return TerminalDecision(
            project_id="demo", run_id=paths.root.name, turn_id=identity.turn_id,
            activation_id=identity.activation_id, endpoint_id=endpoint_id, role="coordinator",
            decision="COMPLETE", summary_ref="terminal/summary_c2.md",
            result_refs=list(result_refs or ["results/worker_result.json"]),
        )

    def make_files(self, paths):
        atomic_write_bytes(paths.terminal / "summary_c2.md", b"Verified worker result and nonce.\n")
        atomic_write_bytes(paths.results / "worker_result.json", b'{"nonce":"RH_FINAL_LOOP_TEST","ack":"WORKER_ACK"}\n')

    def test_valid_complete_decision_commits_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, layout, paths, capsule, coordinator, identity, _ = self.setup_run(Path(temporary))
            self.make_files(paths)
            decision = self.valid_decision(paths, identity)
            layout.write_terminal_decision(paths, capsule, decision)
            target = layout.commit_terminal_decision(paths, capsule, decision, coordinator, identity)
            self.assertTrue(target.exists())
            self.assertEqual(layout.commit_terminal_decision(paths, capsule, decision, coordinator, identity), target)
            recovery = layout.inspect_recovery(paths.root.name)
            self.assertEqual(recovery["status"], "completed")
            self.assertEqual(recovery["next_deterministic_action"], "no_action")
            status = layout.status_summary()
            self.assertEqual(status["health"], "completed")
            self.assertEqual(status["current_workflow"], "terminal")
            self.assertEqual(status["next_role"], "none")
            self.assertIsNone(status["current_owner"])

    def test_chat_complete_text_without_decision_does_not_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, layout, paths, _, _, _, _ = self.setup_run(Path(temporary))
            atomic_write_bytes(paths.logs / "host_c2.txt", b"FULL_LOOP_COMPLETE\n")
            self.assertNotEqual(layout.inspect_recovery(paths.root.name)["status"], "completed")

    def test_identity_authority_and_reference_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, layout, paths, capsule, coordinator, identity, _ = self.setup_run(Path(temporary))
            self.make_files(paths)
            wrong_activation = self.valid_decision(paths, identity)
            wrong_activation.activation_id = "activation_not_registered"
            layout.write_terminal_decision(paths, capsule, wrong_activation)
            with self.assertRaises(IntegrityError):
                layout.commit_terminal_decision(paths, capsule, wrong_activation, coordinator, identity)

            wrong_endpoint = self.valid_decision(paths, identity, endpoint_id="other-endpoint")
            layout.write_terminal_decision(paths, capsule, wrong_endpoint)
            with self.assertRaises(IntegrityError):
                layout.commit_terminal_decision(paths, capsule, wrong_endpoint, coordinator, identity)
            layout.terminal_decision_path(paths, capsule, identity.activation_id).unlink()

            missing_summary = self.valid_decision(paths, identity)
            missing_summary.summary_ref = "terminal/missing.md"
            layout.write_terminal_decision(paths, capsule, missing_summary)
            with self.assertRaises(IntegrityError):
                layout.commit_terminal_decision(paths, capsule, missing_summary, coordinator, identity)
            layout.terminal_decision_path(paths, capsule, identity.activation_id).unlink()

            missing_result = self.valid_decision(paths, identity, result_refs=["results/missing.json"])
            layout.write_terminal_decision(paths, capsule, missing_result)
            with self.assertRaises(IntegrityError):
                layout.commit_terminal_decision(paths, capsule, missing_result, coordinator, identity)

    def test_worker_and_successor_cannot_terminate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, layout, paths, capsule, coordinator, identity, _ = self.setup_run(root, successor_role="worker")
            self.make_files(paths)
            decision = self.valid_decision(paths, identity)
            layout.write_terminal_decision(paths, capsule, decision)
            with self.assertRaises(IntegrityError):
                layout.commit_terminal_decision(paths, capsule, decision, coordinator, identity)

            worker_decision = TerminalDecision(
                "demo", paths.root.name, identity.turn_id, identity.activation_id, "worker-endpoint", "worker",
                "COMPLETE", "terminal/summary_c2.md", ["results/worker_result.json"],
            )
            with self.assertRaises(SchemaError):
                worker_decision.validate()

    def test_durable_decision_without_manifest_commit_is_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, layout, paths, capsule, _, identity, _ = self.setup_run(Path(temporary))
            self.make_files(paths)
            decision = self.valid_decision(paths, identity)
            layout.write_terminal_decision(paths, capsule, decision)
            recovery = layout.inspect_recovery(paths.root.name)
            self.assertTrue(recovery["terminal_commit_pending"])
            self.assertEqual(recovery["next_deterministic_action"], "terminal_commit_pending")
            self.assertNotEqual(recovery["status"], "completed")

    def test_terminal_activation_cannot_emit_same_turn_continuation(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, layout, paths, capsule, coordinator, identity, capsule_path = self.setup_run(Path(temporary))
            self.make_files(paths)
            mailbox = layout.mailbox(paths)
            payload_ref, digest = mailbox.publish_payload("conflict.md", b"unexpected continuation")
            message = MailboxMessage(
                "conflicting-message", paths.root.name, identity.turn_id, "coordinator", "worker", "task",
                payload_ref, str(capsule_path.relative_to(paths.root)).replace("\\", "/"), digest,
            )
            mailbox.publish_message(message)
            decision = self.valid_decision(paths, identity)
            layout.write_terminal_decision(paths, capsule, decision)
            with self.assertRaises(IntegrityError):
                layout.commit_terminal_decision(paths, capsule, decision, coordinator, identity)


def atomic_read_manifest(path: Path):
    import json
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
