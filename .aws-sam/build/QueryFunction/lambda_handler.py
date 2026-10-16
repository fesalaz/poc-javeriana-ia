"""
Handler de AWS Lambda para los endpoints de API Gateway. Traduce cada evento
al contrato del componente correspondiente y devuelve la respuesta HTTP.
Rutas soportadas (todas en la misma función Lambda — monolito modular D2):

- POST /query                          -> Orquestador RAG/CAG (FR1/FR2/FR4)
- POST /courses                        -> crear curso (FR5)
- GET  /courses?userId=...             -> listar cursos de un usuario (FR5)
- POST /courses/{courseId}/teachers    -> asignar docente (FR5)
- POST /courses/{courseId}/students    -> inscribir estudiante (FR5)
- POST /courses/{courseId}/material    -> publicar material: dispara ingesta
                                           RAG + precarga CAG (FR5, integra
                                           con FR4) — reemplaza el rodeo de
                                           scripts/seed_rag_data.py

Vista de despliegue: CloudFront+S3 -> API Gateway -> Lambda (Iteración 1/3).

Usa PGVectorStore (PostgreSQL+pgvector) y DynamoCAGStore reales cuando las
variables de entorno POSTGRES_HOST / DYNAMO_TABLE_NAME están presentes (así se
despliega con infra/template.yaml); si no están (ejecución local sin esa
infraestructura), cae a las implementaciones en memoria — mismo patrón de
"degradación explícita" usado en el resto del proyecto.

Antes de construir el Orquestador, `_load_secrets_into_env()` lee el secreto
de Secrets Manager (SECRETS_ARN) con las credenciales de los 3 proveedores
LLM y la contraseña de PostgreSQL, y `_build_postgres_url()` ensambla
POSTGRES_URL a partir de esas piezas — ver GUIA_DESPLIEGUE_Y_PRUEBAS.md,
sección 6, para el detalle de por qué esto es necesario. Las funciones que no
requieren red/AWS (`_build_postgres_url`, y `_load_secrets_into_env` cuando
SECRETS_ARN no está definido) SÍ se prueban en `tests/test_lambda_handler_helpers.py`;
el resto de este módulo requiere el runtime de Lambda y no se ejecuta aquí.

NOTA (agosto 2026 — hangs fuera de la cadena de proveedores LLM, ver
GUIA_DESPLIEGUE_Y_PRUEBAS.md sección 0.2): CloudWatch Logs mostraron
invocaciones con `Status: timeout` a los 28000ms exactos SIN ningún log
intermedio (ni siquiera el warning de Gemini que sí aparecía en el hallazgo
anterior) — es decir, el colgado ocurre ANTES de llamar a cualquier
proveedor LLM, durante la construcción del Orquestador (`_build_real_orchestrator()`,
cacheada en `_orchestrator`: si la construcción se cuelga, nunca se completa
la caché y CADA invocación —fría o caliente— la reintenta desde cero, lo que
calza con ver el mismo colgado exacto en invocaciones consecutivas). El
sospechoso principal es `PGVectorStore(...)`: `langchain_postgres.PGVector`
suele abrir una conexión real a Postgres (y validar/crear su esquema) al
construirse, y esa conexión no tenía ningún `connect_timeout` — si RDS no es
alcanzable (regla de seguridad, instancia aún inicializando, etc.), psycopg
puede quedarse esperando muchísimo más que los timeouts que ya se
aplicaron a los proveedores LLM (esos NO cubren este paso, que ocurre antes).
Por eso `_build_postgres_url()` ahora agrega `connect_timeout` a la URL que
ensambla, y los clientes de boto3 (Secrets Manager, y `DynamoCAGStore` en
`src/cag/dynamo_cache_store.py`) también fijan timeouts explícitos, para que
cualquier paso de red antes de la cadena de proveedores falle rápido en vez
de colgarse en silencio.

NOTA (agosto 2026 — checkpoints de diagnóstico, deliberadamente permanentes):
tras aplicar los timeouts anteriores, CloudWatch Logs siguió mostrando el
mismo patrón (`Status: timeout` a los 28000ms exactos, sin ningún log
intermedio), es decir, no alcanzó para confirmar cuál paso específico se
sigue colgando. Cuando Lambda mata una invocación por Timeout no queda
ningún traceback (a diferencia de una excepción real), así que la única
forma de ver DÓNDE se va el tiempo es instrumentar cada paso con su propio
log. Por eso `_build_real_orchestrator()` y `handler()` imprimen un
checkpoint con el tiempo transcurrido en cada etapa (`_load_secrets_into_env`,
`_build_postgres_url`, construcción de `PGVectorStore`/`DynamoCAGStore`,
`handle_query`). Esto se deja de forma permanente (no es código de depuración
a borrar después): el costo de unas líneas de `print()` es insignificante
frente a quedarse otra vez sin ninguna pista si el problema reaparece en
producción. Ver GUIA_DESPLIEGUE_Y_PRUEBAS.md, sección 0.2.

NOTA (agosto 2026 — FR5 expuesto como API real): `CourseCatalogService` (en
memoria) no sirve detrás de un Lambda real — cada invocación fría arranca
con un catálogo vacío y las invocaciones concurrentes no comparten memoria
entre sí. Por eso las rutas de `/courses` usan `PostgresCourseCatalogService`
(`src/courses/postgres_catalog.py`) cuando `POSTGRES_HOST` está presente —
mismo criterio de detección que ya se usa para `PGVectorStore`/`DynamoCAGStore`.
El catálogo y el `ResourceManager` se cachean por separado del Orquestador
(`_course_state`, no `_orchestrator`) pero reutilizan su mismo
`vector_store`/`cag_store`/`embedding_provider` — así publicar material
(`POST /courses/{courseId}/material`) dispara la MISMA ingesta RAG y precarga
CAG que antes solo se podía probar con `scripts/seed_rag_data.py`.
"""
import json
import os
import time

from src.config import build_course_catalog, build_orchestrator, build_provider_registry
from src.courses.catalog import CourseNotFoundError, DuplicateCourseError
from src.courses.resource_manager import ResourceManager
from src.orchestrator.orchestrator import QueueFullError

_orchestrator = None
_course_state = None  # (catalog, resource_manager), cacheado por separado del Orquestador


def _checkpoint(label: str, start: float) -> None:
    """Log de diagnóstico con el tiempo transcurrido desde `start`. Ver nota
    de módulo: sin esto, un colgado que dispara el Timeout de Lambda no deja
    ninguna pista de en qué línea se quedó."""
    print(f"[orchestrator-init] {label} (+{time.monotonic() - start:.2f}s)")


def _load_secrets_into_env():
    """Lee el secreto de Secrets Manager (`SECRETS_ARN`, inyectado por
    infra/template.yaml) y vierte sus claves como variables de entorno
    (GOOGLE_API_KEY, OPENAI_OSS_API_KEY, LLAMA_API_KEY, POSTGRES_PASSWORD)
    ANTES de construir los adaptadores/el store — son ellos quienes las leen
    vía os.getenv(). Sin este paso, el secreto se crea pero nunca se usa (gap
    real detectado: la plantilla concedía permiso `GetSecretValue` pero nada
    llamaba a la API). Si `SECRETS_ARN` no está definido (ejecución local),
    no hace nada: cada adaptador ya cae a sus defaults/variables locales."""
    secrets_arn = os.getenv("SECRETS_ARN")
    if not secrets_arn:
        return
    try:
        import boto3
        from botocore.config import Config

        # Timeouts explícitos: los defaults de botocore (60s conectar + 60s
        # leer, con reintentos) pueden colgar la construcción del Orquestador
        # mucho más de lo que el resto del sistema espera. Ver nota de módulo.
        client = boto3.client(
            "secretsmanager",
            config=Config(connect_timeout=5, read_timeout=8, retries={"max_attempts": 1}),
        )
        secret = json.loads(client.get_secret_value(SecretId=secrets_arn)["SecretString"])
        for key, value in secret.items():
            os.environ.setdefault(key, value)  # no pisa variables ya definidas explícitamente
    except Exception as exc:  # noqa: BLE001
        # No tumbar el cold start por un fallo al leer el secreto; los
        # health_check() de cada adaptador reportarán la falla de auth después.
        print(f"Aviso: no se pudo leer SECRETS_ARN ({secrets_arn}): {exc}")


def _build_postgres_url():
    """Ensambla POSTGRES_URL (lo que realmente lee PGVectorStore) a partir de
    host/puerto/db/usuario que inyecta infra/template.yaml, más la contraseña
    que _load_secrets_into_env() acaba de poner en POSTGRES_PASSWORD. Si ya
    hay un POSTGRES_URL explícito (p.ej. ejecución local con docker-compose),
    se respeta y no se sobreescribe."""
    if os.getenv("POSTGRES_URL"):
        return
    host = os.getenv("POSTGRES_HOST")
    if not host:
        return
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "javeriana_ia")
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")
    # connect_timeout (parámetro estándar de libpq/psycopg, no específico de
    # SQLAlchemy) evita que una RDS inalcanzable (regla de seguridad,
    # instancia aún inicializando, etc.) cuelgue la construcción del
    # Orquestador — ver nota de módulo sobre el hang detectado en producción,
    # que ocurría ANTES de llegar a los proveedores LLM.
    connect_timeout = os.getenv("POSTGRES_CONNECT_TIMEOUT_SECONDS", "5")
    os.environ["POSTGRES_URL"] = (
        f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"
        f"?connect_timeout={connect_timeout}"
    )


def _build_real_orchestrator():
    t0 = time.monotonic()
    _checkpoint("inicio de _build_real_orchestrator", t0)

    _load_secrets_into_env()
    _checkpoint("_load_secrets_into_env() completado", t0)
    _build_postgres_url()
    _checkpoint("_build_postgres_url() completado", t0)

    use_real = os.getenv("USE_REAL_LLM_PROVIDERS", "true").lower() == "true"
    registry = build_provider_registry(use_real_providers=use_real)
    _checkpoint("build_provider_registry() completado", t0)

    vector_store = None
    if os.getenv("POSTGRES_HOST"):
        _checkpoint("construyendo PGVectorStore (conecta a RDS)...", t0)
        from src.retrieval.pgvector_store import PGVectorStore
        vector_store = PGVectorStore(embedding_provider=registry)
        _checkpoint("PGVectorStore construido", t0)

    cag_store = None
    if os.getenv("DYNAMO_TABLE_NAME"):
        _checkpoint("construyendo DynamoCAGStore...", t0)
        from src.cag.dynamo_cache_store import DynamoCAGStore
        cag_store = DynamoCAGStore()
        _checkpoint("DynamoCAGStore construido", t0)

    orchestrator = build_orchestrator(
        use_real_providers=use_real, vector_store=vector_store,
        cag_store=cag_store, registry=registry,
    )
    _checkpoint("build_orchestrator() completado — fin de _build_real_orchestrator", t0)
    return orchestrator


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = _build_real_orchestrator()
    return _orchestrator


def _build_course_management():
    """Ensambla el catálogo de cursos (FR5) y el ResourceManager, reutilizando
    el vector_store/cag_store/embedding_provider del Orquestador ya cacheado
    en vez de duplicar conexiones a RDS/DynamoDB. Ver nota de módulo."""
    orchestrator = _get_orchestrator()
    use_real_catalog = bool(os.getenv("POSTGRES_HOST"))
    catalog = build_course_catalog(use_real=use_real_catalog)
    resource_manager = ResourceManager(
        catalog=catalog,
        vector_store=orchestrator.vector_store,
        cag_store=orchestrator.cag_store,
        embedding_provider=orchestrator.embedding_provider,
    )
    return catalog, resource_manager


def _get_course_management():
    global _course_state
    if _course_state is None:
        _course_state = _build_course_management()
    return _course_state


def _json_response(status_code: int, payload: dict, headers: dict = None) -> dict:
    response = {"statusCode": status_code, "body": json.dumps(payload, ensure_ascii=False)}
    if headers:
        response["headers"] = headers
    return response


def _route_query(event: dict) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
        student_id = body["studentId"]
        course_id = body["courseId"]
        message = body["message"]
    except (KeyError, json.JSONDecodeError) as exc:
        return _json_response(400, {"error": f"payload inválido: {exc}"})

    try:
        orchestrator = _get_orchestrator()
    except Exception as exc:  # noqa: BLE001
        # Antes este error NO se atrapaba (solo el bloque de abajo, alrededor
        # de handle_query, tenía try/except) — si _build_real_orchestrator()
        # fallaba (en vez de colgarse) el Lambda terminaba en un crash sin
        # respuesta HTTP clara. Ver nota de módulo sobre los checkpoints.
        print(f"[orchestrator-init] FALLÓ: {exc}")
        return _json_response(502, {"error": f"fallo al inicializar: {exc}"})

    t_query = time.monotonic()
    try:
        result = orchestrator.handle_query(student_id, course_id, message)
        print(f"[handle_query] completado (+{time.monotonic() - t_query:.2f}s)")
        return _json_response(200, result)
    except QueueFullError:
        return _json_response(
            503, {"error": "sistema saturado, intenta de nuevo en unos segundos"},
            headers={"Retry-After": "5"},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[handle_query] FALLÓ tras +{time.monotonic() - t_query:.2f}s: {exc}")
        return _json_response(502, {"error": str(exc)})


def _route_create_course(event: dict) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
        course_id = body["courseId"]
        name = body["name"]
    except (KeyError, json.JSONDecodeError) as exc:
        return _json_response(400, {"error": f"payload inválido: {exc}"})

    try:
        catalog, _ = _get_course_management()
        catalog.create_course(course_id, name)
        return _json_response(201, {"courseId": course_id, "name": name})
    except DuplicateCourseError:
        return _json_response(409, {"error": f"el curso '{course_id}' ya existe"})
    except Exception as exc:  # noqa: BLE001
        return _json_response(502, {"error": str(exc)})


def _route_list_courses(event: dict) -> dict:
    query_params = event.get("queryStringParameters") or {}
    user_id = query_params.get("userId")
    if not user_id:
        return _json_response(400, {"error": "falta el parámetro de consulta 'userId'"})

    try:
        catalog, _ = _get_course_management()
        courses = catalog.list_courses(user_id)
        return _json_response(200, {
            "courses": [
                {"courseId": c.course_id, "name": c.name,
                 "teachers": sorted(c.teachers), "students": sorted(c.students)}
                for c in courses
            ]
        })
    except Exception as exc:  # noqa: BLE001
        return _json_response(502, {"error": str(exc)})


def _route_assign_teacher(event: dict) -> dict:
    course_id = (event.get("pathParameters") or {}).get("courseId")
    try:
        body = json.loads(event.get("body") or "{}")
        teacher_id = body["teacherId"]
    except (KeyError, json.JSONDecodeError) as exc:
        return _json_response(400, {"error": f"payload inválido: {exc}"})

    try:
        catalog, _ = _get_course_management()
        catalog.assign_teacher(course_id, teacher_id)
        return _json_response(200, {"courseId": course_id, "teacherId": teacher_id})
    except CourseNotFoundError:
        return _json_response(404, {"error": f"el curso '{course_id}' no existe"})
    except Exception as exc:  # noqa: BLE001
        return _json_response(502, {"error": str(exc)})


def _route_enroll_student(event: dict) -> dict:
    course_id = (event.get("pathParameters") or {}).get("courseId")
    try:
        body = json.loads(event.get("body") or "{}")
        student_id = body["studentId"]
    except (KeyError, json.JSONDecodeError) as exc:
        return _json_response(400, {"error": f"payload inválido: {exc}"})

    try:
        catalog, _ = _get_course_management()
        catalog.enroll_student(course_id, student_id)
        return _json_response(200, {"courseId": course_id, "studentId": student_id})
    except CourseNotFoundError:
        return _json_response(404, {"error": f"el curso '{course_id}' no existe"})
    except Exception as exc:  # noqa: BLE001
        return _json_response(502, {"error": str(exc)})


def _route_publish_material(event: dict) -> dict:
    course_id = (event.get("pathParameters") or {}).get("courseId")
    try:
        body = json.loads(event.get("body") or "{}")
        document_id = body["documentId"]
        text = body["text"]
        cag_context = body.get("cagContext")
        cag_ttl_seconds = body.get("cagTtlSeconds")
    except (KeyError, json.JSONDecodeError) as exc:
        return _json_response(400, {"error": f"payload inválido: {exc}"})

    try:
        _, resource_manager = _get_course_management()
        result = resource_manager.publish_material(
            course_id=course_id, document_id=document_id, text=text,
            cag_context=cag_context, cag_ttl_seconds=cag_ttl_seconds,
        )
        return _json_response(200, result)
    except CourseNotFoundError:
        return _json_response(404, {"error": f"el curso '{course_id}' no existe"})
    except Exception as exc:  # noqa: BLE001
        return _json_response(502, {"error": str(exc)})


# Enrutamiento por (resource, método HTTP) — todas las rutas viven en la misma
# función Lambda (decisión D2, monolito modular). `resource` es la plantilla
# de ruta que define infra/template.yaml (p.ej. "/courses/{courseId}/material"),
# no la ruta ya resuelta con valores reales (esa es event["path"]).
_ROUTES = {
    ("/query", "POST"): _route_query,
    ("/courses", "POST"): _route_create_course,
    ("/courses", "GET"): _route_list_courses,
    ("/courses/{courseId}/teachers", "POST"): _route_assign_teacher,
    ("/courses/{courseId}/students", "POST"): _route_enroll_student,
    ("/courses/{courseId}/material", "POST"): _route_publish_material,
}


def handler(event, context):
    resource = event.get("resource") or event.get("path", "")
    method = event.get("httpMethod", "POST")
    route = _ROUTES.get((resource, method))
    if route is None:
        return _json_response(404, {"error": f"ruta no encontrada: {method} {resource}"})
    return route(event)
