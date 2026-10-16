"""Verifica los helpers de lambda_handler.py que resuelven credenciales desde
AWS Secrets Manager (SECRETS_ARN) y ensamblan POSTGRES_URL a partir de las
variables sueltas que inyecta infra/template.yaml (POSTGRES_HOST/PORT/DB/USER
+ la contraseña que llega dentro del secreto). Bug real detectado y corregido:
antes de esto, el Lambda desplegado nunca leía las API keys ni la contraseña
de PostgreSQL, así que se habría conectado a `localhost` y sin credenciales
LLM. Ver GUIA_DESPLIEGUE_Y_PRUEBAS.md, sección 6."""
import json
import os
import sys
import types
import unittest
from unittest.mock import patch

import lambda_handler


def _fake_boto3_modules(client_factory):
    """Construye módulos falsos de boto3/botocore.config para inyectar en
    sys.modules (ninguno de los dos está instalado en este sandbox). Incluye
    botocore.config.Config porque _load_secrets_into_env() ahora pasa
    timeouts explícitos vía `config=Config(...)` (ver nota de módulo en
    lambda_handler.py sobre el hang detectado en producción)."""
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = client_factory

    fake_botocore = types.ModuleType("botocore")
    fake_botocore_config = types.ModuleType("botocore.config")

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_botocore_config.Config = _FakeConfig
    fake_botocore.config = fake_botocore_config

    return {
        "boto3": fake_boto3,
        "botocore": fake_botocore,
        "botocore.config": fake_botocore_config,
    }


class TestBuildPostgresUrl(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_noop_if_postgres_url_already_set(self):
        os.environ["POSTGRES_URL"] = "postgresql+psycopg://ya:definido@host/db"
        os.environ["POSTGRES_HOST"] = "otro-host"
        lambda_handler._build_postgres_url()
        self.assertEqual(os.environ["POSTGRES_URL"], "postgresql+psycopg://ya:definido@host/db")

    def test_noop_if_no_postgres_host(self):
        os.environ.pop("POSTGRES_URL", None)
        os.environ.pop("POSTGRES_HOST", None)
        lambda_handler._build_postgres_url()
        self.assertNotIn("POSTGRES_URL", os.environ)

    def test_builds_url_from_parts_injected_by_template(self):
        os.environ.pop("POSTGRES_URL", None)
        os.environ["POSTGRES_HOST"] = "mi-rds.amazonaws.com"
        os.environ["POSTGRES_PORT"] = "5432"
        os.environ["POSTGRES_DB"] = "javeriana_ia"
        os.environ["POSTGRES_USER"] = "javeriana_app"
        os.environ["POSTGRES_PASSWORD"] = "s3cr3t"
        lambda_handler._build_postgres_url()
        self.assertEqual(
            os.environ["POSTGRES_URL"],
            "postgresql+psycopg://javeriana_app:s3cr3t@mi-rds.amazonaws.com:5432/javeriana_ia?connect_timeout=5",
        )

    def test_builds_url_uses_custom_connect_timeout_env_var(self):
        """El timeout de conexión a Postgres es configurable (mismo patrón que
        LLM_REQUEST_TIMEOUT_SECONDS), no un valor fijo en el código."""
        os.environ.pop("POSTGRES_URL", None)
        os.environ["POSTGRES_HOST"] = "mi-rds.amazonaws.com"
        os.environ["POSTGRES_CONNECT_TIMEOUT_SECONDS"] = "3"
        lambda_handler._build_postgres_url()
        self.assertIn("connect_timeout=3", os.environ["POSTGRES_URL"])


class TestLoadSecretsIntoEnv(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)
        sys.modules.pop("boto3", None)
        sys.modules.pop("botocore", None)
        sys.modules.pop("botocore.config", None)

    def test_noop_if_no_secrets_arn(self):
        os.environ.pop("SECRETS_ARN", None)
        lambda_handler._load_secrets_into_env()
        self.assertNotIn("GOOGLE_API_KEY", os.environ)

    def test_loads_all_secret_keys_into_env(self):
        os.environ["SECRETS_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:fake"
        secret_payload = json.dumps({
            "GOOGLE_API_KEY": "g-key",
            "OPENAI_OSS_API_KEY": "o-key",
            "LLAMA_API_KEY": "l-key",
            "POSTGRES_PASSWORD": "s3cr3t",
        })
        fake_modules = _fake_boto3_modules(
            lambda service_name, config=None: types.SimpleNamespace(
                get_secret_value=lambda SecretId: {"SecretString": secret_payload}
            )
        )
        with patch.dict(sys.modules, fake_modules):
            lambda_handler._load_secrets_into_env()

        self.assertEqual(os.environ["GOOGLE_API_KEY"], "g-key")
        self.assertEqual(os.environ["OPENAI_OSS_API_KEY"], "o-key")
        self.assertEqual(os.environ["LLAMA_API_KEY"], "l-key")
        self.assertEqual(os.environ["POSTGRES_PASSWORD"], "s3cr3t")

    def test_existing_env_vars_take_precedence_over_secret(self):
        os.environ["SECRETS_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:fake"
        os.environ["GOOGLE_API_KEY"] = "ya-estaba-en-el-entorno"
        secret_payload = json.dumps({"GOOGLE_API_KEY": "del-secreto"})
        fake_modules = _fake_boto3_modules(
            lambda service_name, config=None: types.SimpleNamespace(
                get_secret_value=lambda SecretId: {"SecretString": secret_payload}
            )
        )
        with patch.dict(sys.modules, fake_modules):
            lambda_handler._load_secrets_into_env()

        self.assertEqual(os.environ["GOOGLE_API_KEY"], "ya-estaba-en-el-entorno")

    def test_read_failure_does_not_raise(self):
        os.environ["SECRETS_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:fake"

        def _raise(service_name, config=None):
            raise RuntimeError("fallo simulado de red hacia Secrets Manager")

        fake_modules = _fake_boto3_modules(_raise)
        with patch.dict(sys.modules, fake_modules):
            lambda_handler._load_secrets_into_env()  # no debe lanzar excepción
        self.assertNotIn("GOOGLE_API_KEY", os.environ)

    def test_secrets_client_configured_with_bounded_timeouts(self):
        """Caso real detectado: sin timeouts explícitos, un cliente de boto3
        colgado puede consumir el Timeout completo del Lambda ANTES de llegar
        a la cadena de proveedores LLM (que ya tiene su propio timeout de
        pared). Verifica que se le pase `config=` con connect/read timeout."""
        os.environ["SECRETS_ARN"] = "arn:aws:secretsmanager:us-east-1:123:secret:fake"
        secret_payload = json.dumps({"GOOGLE_API_KEY": "g-key"})
        seen_configs = []

        def _client(service_name, config=None):
            seen_configs.append(config)
            return types.SimpleNamespace(
                get_secret_value=lambda SecretId: {"SecretString": secret_payload}
            )

        fake_modules = _fake_boto3_modules(_client)
        with patch.dict(sys.modules, fake_modules):
            lambda_handler._load_secrets_into_env()

        self.assertEqual(len(seen_configs), 1)
        self.assertIsNotNone(seen_configs[0])
        self.assertIn("connect_timeout", seen_configs[0].kwargs)
        self.assertIn("read_timeout", seen_configs[0].kwargs)


class TestHandlerResilience(unittest.TestCase):
    """Bug real corregido: antes, si _get_orchestrator() (construcción del
    Orquestador real) lanzaba una excepción, handler() la dejaba propagar sin
    atraparla (el try/except solo envolvía handle_query()) — el Lambda
    terminaba en un crash sin ninguna respuesta HTTP clara. Ver la nota de
    módulo sobre los checkpoints de diagnóstico y por qué esto importa
    incluso cuando el problema real es un colgado (Timeout) y no una
    excepción: al menos las fallas que SÍ lanzan quedan bien manejadas."""

    def _valid_event(self):
        return {
            "resource": "/query", "httpMethod": "POST",
            "body": json.dumps({
                "studentId": "est-1", "courseId": "curso-1", "message": "hola",
            }),
        }

    def test_orchestrator_init_failure_returns_502_not_crash(self):
        with patch.object(
            lambda_handler, "_get_orchestrator", side_effect=RuntimeError("no se pudo conectar a RDS")
        ):
            response = lambda_handler.handler(self._valid_event(), None)
        self.assertEqual(response["statusCode"], 502)
        self.assertIn("no se pudo conectar a RDS", json.loads(response["body"])["error"])

    def test_successful_query_returns_200(self):
        fake_orchestrator = types.SimpleNamespace(
            handle_query=lambda student_id, course_id, message: {
                "response": "ok", "source": "llm:mock", "used_cag": False, "rag_chunks": 0,
            }
        )
        with patch.object(lambda_handler, "_get_orchestrator", return_value=fake_orchestrator):
            response = lambda_handler.handler(self._valid_event(), None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["response"], "ok")

    def test_unknown_route_returns_404(self):
        response = lambda_handler.handler(
            {"resource": "/no-existe", "httpMethod": "POST", "body": "{}"}, None
        )
        self.assertEqual(response["statusCode"], 404)


class TestCourseRoutes(unittest.TestCase):
    """Rutas nuevas de gestión de cursos (FR5, ver nota de módulo). Se prueban
    con el catálogo EN MEMORIA (mismo usado en tests/test_resource_manager.py)
    inyectado vía _get_course_management — PostgresCourseCatalogService
    (src/courses/postgres_catalog.py) requiere PostgreSQL real y no se
    ejecuta en este entorno, igual que PGVectorStore/DynamoCAGStore."""

    def setUp(self):
        from src.adapters.mock_adapter import MockLLMAdapter
        from src.cag.cache_store import CAGStore
        from src.courses.catalog import CourseCatalogService
        from src.courses.resource_manager import ResourceManager
        from src.retrieval.inmemory_store import InMemoryVectorStore

        self.catalog = CourseCatalogService()
        self.vector_store = InMemoryVectorStore()
        self.cag_store = CAGStore()
        self.resource_manager = ResourceManager(
            catalog=self.catalog, vector_store=self.vector_store,
            cag_store=self.cag_store, embedding_provider=MockLLMAdapter(name="gemini"),
        )
        self._patcher = patch.object(
            lambda_handler, "_get_course_management",
            return_value=(self.catalog, self.resource_manager),
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def _event(self, resource, method, body=None, path_params=None, query=None):
        event = {"resource": resource, "httpMethod": method}
        if body is not None:
            event["body"] = json.dumps(body)
        if path_params is not None:
            event["pathParameters"] = path_params
        if query is not None:
            event["queryStringParameters"] = query
        return event

    def test_create_course_returns_201(self):
        response = lambda_handler.handler(
            self._event("/courses", "POST", {"courseId": "ISIS-1234", "name": "Arquitectura"}),
            None,
        )
        self.assertEqual(response["statusCode"], 201)
        self.catalog.get_course("ISIS-1234")  # no lanza

    def test_create_duplicate_course_returns_409(self):
        self.catalog.create_course("ISIS-1234", "Arquitectura")
        response = lambda_handler.handler(
            self._event("/courses", "POST", {"courseId": "ISIS-1234", "name": "Otra"}),
            None,
        )
        self.assertEqual(response["statusCode"], 409)

    def test_create_course_missing_field_returns_400(self):
        response = lambda_handler.handler(
            self._event("/courses", "POST", {"courseId": "ISIS-1234"}),  # falta 'name'
            None,
        )
        self.assertEqual(response["statusCode"], 400)

    def test_list_courses_returns_200(self):
        self.catalog.create_course("ISIS-1234", "Arquitectura")
        self.catalog.enroll_student("ISIS-1234", "est-1")
        response = lambda_handler.handler(
            self._event("/courses", "GET", query={"userId": "est-1"}), None
        )
        self.assertEqual(response["statusCode"], 200)
        courses = json.loads(response["body"])["courses"]
        self.assertEqual([c["courseId"] for c in courses], ["ISIS-1234"])

    def test_list_courses_missing_user_id_returns_400(self):
        response = lambda_handler.handler(self._event("/courses", "GET", query={}), None)
        self.assertEqual(response["statusCode"], 400)

    def test_assign_teacher_returns_200(self):
        self.catalog.create_course("ISIS-1234", "Arquitectura")
        response = lambda_handler.handler(
            self._event(
                "/courses/{courseId}/teachers", "POST", {"teacherId": "prof-1"},
                path_params={"courseId": "ISIS-1234"},
            ),
            None,
        )
        self.assertEqual(response["statusCode"], 200)
        self.assertIn("prof-1", self.catalog.get_course("ISIS-1234").teachers)

    def test_assign_teacher_unknown_course_returns_404(self):
        response = lambda_handler.handler(
            self._event(
                "/courses/{courseId}/teachers", "POST", {"teacherId": "prof-1"},
                path_params={"courseId": "NO-EXISTE"},
            ),
            None,
        )
        self.assertEqual(response["statusCode"], 404)

    def test_enroll_student_returns_200(self):
        self.catalog.create_course("ISIS-1234", "Arquitectura")
        response = lambda_handler.handler(
            self._event(
                "/courses/{courseId}/students", "POST", {"studentId": "est-1"},
                path_params={"courseId": "ISIS-1234"},
            ),
            None,
        )
        self.assertEqual(response["statusCode"], 200)
        self.assertIn("est-1", self.catalog.get_course("ISIS-1234").students)

    def test_publish_material_returns_200_and_indexes_chunks(self):
        self.catalog.create_course("ISIS-1234", "Arquitectura")
        response = lambda_handler.handler(
            self._event(
                "/courses/{courseId}/material", "POST",
                {"documentId": "doc-1", "text": "contenido del syllabus " * 100},
                path_params={"courseId": "ISIS-1234"},
            ),
            None,
        )
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertGreater(body["chunks_indexed"], 0)

    def test_publish_material_with_cag_context_preloads(self):
        self.catalog.create_course("ISIS-1234", "Arquitectura")
        response = lambda_handler.handler(
            self._event(
                "/courses/{courseId}/material", "POST",
                {"documentId": "doc-1", "text": "contenido", "cagContext": "Reglas: 3 parciales."},
                path_params={"courseId": "ISIS-1234"},
            ),
            None,
        )
        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(json.loads(response["body"])["cag_preloaded"])
        self.assertEqual(self.cag_store.get("ISIS-1234"), "Reglas: 3 parciales.")

    def test_publish_material_unknown_course_returns_404(self):
        response = lambda_handler.handler(
            self._event(
                "/courses/{courseId}/material", "POST",
                {"documentId": "doc-1", "text": "contenido"},
                path_params={"courseId": "NO-EXISTE"},
            ),
            None,
        )
        self.assertEqual(response["statusCode"], 404)


if __name__ == "__main__":
    unittest.main()
