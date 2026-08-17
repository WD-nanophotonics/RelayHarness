from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from relay_harness.config import AgentConfig, ProjectProfile, RepositoryConfig, RuntimeConfig
from relay_harness.errors import IntegrityError, ModelPolicyError, SchemaError
from relay_harness.journal import IncidentRecorder, StructuredJournal
from relay_harness.model_policy import ModelEvidence, ModelPolicy, ModelRequest
from relay_harness.runtime import RuntimeLayout
from relay_harness.schemas import BootstrapCapsule, Claim, ResultCapsule, TaskCapsule
from relay_harness.storage import atomic_write_json, sha256_file, verify_hash


class FoundationTests(unittest.TestCase):
    def profile(self, root: Path) -> ProjectProfile:
        return ProjectProfile(
            project_id="demo",
            repository=RepositoryConfig("../demo", "main"),
            agent=AgentConfig(["agent-wrapper"], "Luna", "High"),
            runtime=RuntimeConfig(str(root / "runtime")),
        )

    def test_profile_is_explicitly_luna_high(self):
        profile = self.profile(Path("."))
        profile.validate()
        with self.assertRaises(SchemaError):
            ProjectProfile("demo", RepositoryConfig("."), AgentConfig(["x"], "Terra", "High")).validate()
        with self.assertRaises(ModelPolicyError):
            ModelPolicy.validate_request(ModelRequest("Sol", "High"))
        ModelPolicy.validate_evidence(ModelEvidence("Luna", "High", "test"))

    def test_atomic_hash_and_capsule_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "value.json"
            atomic_write_json(path, {"b": 2, "a": 1})
            verify_hash(path, sha256_file(path))
            capsule = BootstrapCapsule(
                project_id="demo", run_id="run_1", turn_id="turn_1", role="worker",
                runtime_root=temporary, profile_ref="profiles/demo.json", durable_state_refs={"objective": "state/objective.md"},
                semantic_ref="tasks/task_1.json", repository={"path": "../demo", "branch": "main"},
                expected_git={"head": None, "branch": "main"}, output_contract={"result": "results/*.json"},
                logging_contract={"journal": "logs/journal.jsonl"}, next_role="controller", terminal_contract={"on_exit": "capture"},
            )
            parsed = BootstrapCapsule.from_dict(capsule.to_dict())
            self.assertEqual(parsed.required_model, "Luna")

    def test_runtime_claims_and_recovery_inspection(self):
        with tempfile.TemporaryDirectory() as temporary:
            layout = RuntimeLayout(self.profile(Path(temporary)))
            paths = layout.new_run()
            task = TaskCapsule("demo", paths.root.name, "turn_1", "controller", "state/objective.md", None)
            layout.write_task(paths, task)
            layout.acquire_claim(paths, Claim("task", task.task_id, "controller", 123, "turn_1"))
            with self.assertRaises(IntegrityError):
                layout.acquire_claim(paths, Claim("task", task.task_id, "worker", 456, "turn_2"))
            report = layout.inspect_recovery(paths.root.name)
            self.assertEqual(report["tasks"].__len__(), 1)
            self.assertFalse(report["continuation_structurally_complete"])

    def test_journal_incident_and_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = StructuredJournal(root / "journal.jsonl", {"project": "demo", "run": "run_1"})
            journal.append("ack", pid=99, role="worker")
            self.assertIn('"event": "ack"', (root / "journal.jsonl").read_text(encoding="utf-8"))
            bundle = IncidentRecorder(root / "incidents").record("incident_1", {"type": "stalled", "confidence": 0.8}, [{"pid": 99, "alive": False}])
            self.assertTrue((bundle / "objective_evidence.json").exists())
            result = ResultCapsule("demo", "run_1", "turn_1", "task_1", "worker", "completed", "results/summary.md")
            self.assertEqual(json.loads(json.dumps(result.to_dict()))["status"], "completed")


if __name__ == "__main__":
    unittest.main()
