"""
Prueba de integración de `build_application()`: verifica que el Orquestador,
Gestión de Cursos y Registro de Artefactos trabajen juntos como lo describe el
documento de arquitectura (Iteraciones 1, 2, 4 y 6).
"""
import unittest

from src.config import build_application
from tests.fakes import FakeHttpClient


class TestApplicationIntegration(unittest.TestCase):
    def test_query_after_publishing_material_uses_rag_and_logs_progress(self):
        app = build_application(use_real_providers=False)
        app.catalog.create_course("ISIS-1234", "Arquitectura de Software")
        app.catalog.enroll_student("ISIS-1234", "est-1")

        app.resource_manager.publish_material(
            course_id="ISIS-1234", document_id="syllabus",
            text="El curso cubre ADD 3.0, RAG y CAG. " * 20,
        )

        result = app.orchestrator.handle_query("est-1", "ISIS-1234", "¿qué temas cubre el curso?")
        self.assertTrue(result["source"].startswith("llm:"))

        app.progress_aggregator.aggregate("ISIS-1234")
        progress = app.dashboard.get_progress("est-1", "ISIS-1234")
        self.assertEqual(progress["query_count"], 1)

    def test_artifact_registered_and_invoked_updates_progress(self):
        http = FakeHttpClient(json_data={"reply": "hola", "metadata": {}})
        app = build_application(use_real_providers=False, http_client=http)
        app.catalog.create_course("ISIS-1234", "Arquitectura de Software")
        app.catalog.enroll_student("ISIS-1234", "est-1")

        app.artifact_registry.register_artifact({
            "artifact_id": "art-1", "course_id": "ISIS-1234",
            "name": "Tutor de repaso", "artifact_type": "webhook",
            "url": "https://tutor.example.com/webhook",
        })

        app.artifact_invoker.invoke("art-1", "est-1", "ISIS-1234", "ayuda por favor")

        app.progress_aggregator.aggregate("ISIS-1234")
        progress = app.dashboard.get_progress("est-1", "ISIS-1234")
        self.assertEqual(progress["artifact_invocations"], 1)

    def test_authorization_helper_prevents_cross_course_access(self):
        app = build_application(use_real_providers=False)
        app.catalog.create_course("ISIS-1234", "Curso A")
        app.catalog.create_course("ISIS-5678", "Curso B")
        app.catalog.enroll_student("ISIS-1234", "est-1")

        self.assertTrue(app.catalog.is_enrolled_or_assigned("est-1", "ISIS-1234"))
        self.assertFalse(app.catalog.is_enrolled_or_assigned("est-1", "ISIS-5678"))


if __name__ == "__main__":
    unittest.main()
