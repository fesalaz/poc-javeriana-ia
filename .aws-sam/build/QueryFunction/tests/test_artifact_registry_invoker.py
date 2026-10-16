"""Verifica el Registro de Artefactos y el Invoker (Iteración 4)."""
import unittest

from src.artifacts.invoker import ArtifactInvocationError, ArtifactInvoker
from src.artifacts.registry import ArtifactNotFoundError, ArtifactRegistry
from src.tracking.activity_logger import ActivityLogger
from tests.fakes import FakeHttpClient


def webhook_data(**overrides):
    data = {
        "artifact_id": "art-1",
        "course_id": "ISIS-1234",
        "name": "Tutor de repaso",
        "artifact_type": "webhook",
        "url": "https://tutor.example.com/webhook",
    }
    data.update(overrides)
    return data


def embed_data(**overrides):
    data = {
        "artifact_id": "art-2",
        "course_id": "ISIS-1234",
        "name": "Chatbot embebido",
        "artifact_type": "embed",
        "url": "https://chat.example.com/widget",
        "allowed_domains": ["chat.example.com"],
    }
    data.update(overrides)
    return data


class TestArtifactRegistry(unittest.TestCase):
    def test_register_and_list_by_course(self):
        registry = ArtifactRegistry()
        registry.register_artifact(webhook_data())
        registry.register_artifact(embed_data())
        registry.register_artifact(webhook_data(artifact_id="art-3", course_id="OTRO-999"))

        artifacts = registry.list_artifacts("ISIS-1234")
        self.assertEqual({a.artifact_id for a in artifacts}, {"art-1", "art-2"})

    def test_get_missing_artifact_raises(self):
        registry = ArtifactRegistry()
        with self.assertRaises(ArtifactNotFoundError):
            registry.get_artifact("no-existe")

    def test_new_artifact_is_available_by_default(self):
        registry = ArtifactRegistry()
        registry.register_artifact(webhook_data())
        self.assertTrue(registry.is_available("art-1"))


class TestArtifactInvoker(unittest.TestCase):
    def setUp(self):
        self.registry = ArtifactRegistry()
        self.registry.register_artifact(webhook_data())
        self.registry.register_artifact(embed_data())
        self.logger = ActivityLogger()

    def test_invoke_webhook_success(self):
        http = FakeHttpClient(json_data={"reply": "hola estudiante", "metadata": {"topic": "repaso"}})
        invoker = ArtifactInvoker(self.registry, http_client=http, activity_logger=self.logger)

        result = invoker.invoke("art-1", "est-1", "ISIS-1234", "¿me ayudas a repasar?")

        self.assertEqual(result["reply"], "hola estudiante")
        self.assertEqual(http.calls[0]["url"], "https://tutor.example.com/webhook")
        events = self.logger.events_for_student("ISIS-1234", "est-1")
        self.assertEqual(events[-1].event_type, "artifact_invoked")

    def test_invoke_failure_marks_unavailable_and_logs(self):
        http = FakeHttpClient(should_fail=True)
        invoker = ArtifactInvoker(self.registry, http_client=http, activity_logger=self.logger)

        with self.assertRaises(ArtifactInvocationError):
            invoker.invoke("art-1", "est-1", "ISIS-1234", "pregunta")

        self.assertFalse(self.registry.is_available("art-1"))
        events = self.logger.events_for_student("ISIS-1234", "est-1")
        self.assertEqual(events[-1].event_type, "artifact_invocation_failed")

    def test_invoke_when_unavailable_raises_without_calling_http(self):
        http = FakeHttpClient()
        invoker = ArtifactInvoker(self.registry, http_client=http)
        self.registry.mark_unavailable("art-1")

        with self.assertRaises(ArtifactInvocationError):
            invoker.invoke("art-1", "est-1", "ISIS-1234", "pregunta")
        self.assertEqual(http.calls, [])

    def test_invoke_wrong_type_raises(self):
        http = FakeHttpClient()
        invoker = ArtifactInvoker(self.registry, http_client=http)
        with self.assertRaises(ArtifactInvocationError):
            invoker.invoke("art-2", "est-1", "ISIS-1234", "pregunta")  # art-2 es 'embed'

    def test_get_embed_info(self):
        invoker = ArtifactInvoker(self.registry)
        info = invoker.get_embed_info("art-2")
        self.assertEqual(info["url"], "https://chat.example.com/widget")
        self.assertIn("chat.example.com", info["allowed_domains"])


if __name__ == "__main__":
    unittest.main()
