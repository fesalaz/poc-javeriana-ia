# PoC — Asistente Javeriana IA

Prueba de concepto de la arquitectura documentada en `arquitectura-add30-javeriana.md`
(diseño ADD 3.0). Implementa en código las Iteraciones 1, 2, 3, 4, 5 y 6:

- **Núcleo RAG/CAG**: Router, Retrieval (RAG), Caché KV (CAG), Caché semántico
  de respuestas, Provider Registry con circuit breaker/fallback entre LLMs, y
  el Orquestador que integra todo. El Provider Registry conecta 5 proveedores
  reales en dos cadenas independientes (ver `src/config.py`):
  - `generate()`: Gemini (Google AI Studio, gratuito, primario) ->
    `openai/gpt-oss-20b` vía Groq (gratuito, respaldo 1) -> Meta Llama 3.3
    vía OpenRouter (de pago económico, respaldo 2 — su slug `:free` dejó de
    estar disponible en la práctica, ver `GUIA_DESPLIEGUE_Y_PRUEBAS.md`
    sección 0.1). Sin cambios desde agosto de 2026.
  - `embed()`: Gemini (primario) -> **AWS Bedrock, Titan Text Embeddings V2
    (respaldo 1, agregado septiembre 2026)** -> **Voyage AI, voyage-4-lite
    (respaldo 2)**. Se agregó tras un hallazgo real: bajo una prueba de carga
    de 100 usuarios concurrentes, solo Gemini implementaba `embed()` entre
    los 3 proveedores originales — cuando su circuit breaker se abrió, todo
    el pipeline RAG falló por falta de un respaldo funcional (hallazgo R-7,
    ver `EVALUACION_ATAM_LITE.md`). `OpenAIAdapter`/`LlamaAdapter` siguen sin
    soportar `embed()`, y `BedrockEmbeddingAdapter`/`VoyageAdapter` no
    soportan `generate()` — ambos se saltan sin contar contra su circuit
    breaker en la cadena que no les corresponde (mismo mecanismo que ya
    existía para "no soporta esta operación").
- **Registro de Artefactos Docentes** (Iteración 4): manifest validado, modos
  *embed* y *webhook*, invocación con manejo de fallas.
- **Gestión de Cursos y Seguimiento** (Iteración 6): catálogo de cursos,
  publicación de material (dispara ingesta RAG + precarga CAG), Activity
  Logger, Progress Aggregator y Dashboard API.
- **API real de gestión de cursos (FR5)**: a diferencia de Artefactos (FR3) y
  Seguimiento (FR6), que hoy solo existen como clases Python, FR5 ya está
  expuesto como endpoints reales de API Gateway (`POST /courses`,
  `GET /courses`, `POST /courses/{courseId}/teachers`,
  `POST /courses/{courseId}/students`, `POST /courses/{courseId}/material`),
  respaldados por `PostgresCourseCatalogService` (persistente, sobre la misma
  RDS que usa RAG) — no por el catálogo en memoria, que se pierde entre
  invocaciones de Lambda. Ver `GUIA_DESPLIEGUE_Y_PRUEBAS.md`, sección 6.5.

**Autenticación (parcial desde septiembre 2026):** las 6 rutas de la API
(`/query` y las 5 de `/courses...`) ya requieren un JWT válido de Cognito —
`infra/template.yaml` conectó el `UserPool` (que antes existía como recurso
suelto, sin usar) como autorizador de una API Gateway explícita
(`JaverianaApi`). Ver `GUIA_DESPLIEGUE_Y_PRUEBAS.md`, sección 6.6, para
obtener un token (por ejemplo, para probar la API con Postman).

**Autorización real (RBAC por curso) sigue sin implementar.** El token de
Cognito valida *quién* llama, no *qué* puede hacer: `lambda_handler.py`
sigue confiando en el `studentId`/`courseId`/`teacherId` del body de la
petición sin cruzarlo contra la matrícula real. El resto de la Iteración 7
(el "Authorization Policy Engine" con RBAC sobre `courseId`, y la
federación del `UserPool` con el IdP institucional) sigue diseñado en el
documento de arquitectura pero no implementado en código —
`CourseCatalogService` expone `is_enrolled_or_assigned()`, que es el mismo
mecanismo que la Iteración 7 describe para formalizar ese control de
acceso, listo para conectarse a una capa de autorización real.

## Cómo se verificó (sin credenciales ni internet)

Este proyecto se construyó y probó en un entorno sin acceso a paquetes externos
(no hay `langchain`, `psycopg2`, etc. instalados, ni conexión a las APIs de
Gemini/Llama, ni a ningún webhook externo). Para poder **verificar la lógica
de la arquitectura** de todos modos, cada pieza que depende de esas librerías
o de red tiene una implementación de respaldo que cumple el mismo contrato
(mismo patrón *Ports & Adapters* que ya usa el diseño):

| Puerto (interfaz) | Implementación real (tu entorno, con internet) | Implementación de verificación (usada en `tests/`) |
|---|---|---|
| `LLMProviderPort` | `GeminiAdapter`, `OpenAIAdapter` (gpt-oss-20b/Groq), `LlamaAdapter` (3.3/OpenRouter) vía LangChain; `BedrockEmbeddingAdapter` (Titan Embeddings V2, vía boto3) y `VoyageAdapter` (voyage-4-lite, vía `requests`) — estos 2 últimos solo implementan `embed()` | `MockLLMAdapter` (uno por proveedor) |
| `VectorStorePort` | `PGVectorStore` (PostgreSQL + pgvector) | `InMemoryVectorStore` |
| Caché KV para CAG | `DynamoCAGStore` (Amazon DynamoDB) | `CAGStore` (en memoria) |
| Text splitter (ingesta) | `RecursiveCharacterTextSplitter` (LangChain) | splitter por caracteres, si LangChain no está instalado |
| Cliente HTTP (invocación de artefactos webhook) | `requests` (o cualquiera con `.post()`) | `FakeHttpClient` (`tests/fakes.py`) |

`lambda_handler.py` elige automáticamente la implementación real cuando
detecta las variables de entorno `POSTGRES_HOST`/`DYNAMO_TABLE_NAME` (las que
pone `infra/template.yaml` al desplegar); si no están, usa las de en memoria.

**95 pruebas unitarias** (`tests/`, con `unittest` de la librería estándar) cubren:
máquina de estados del circuit breaker, fallback entre proveedores (incluida la
distinción entre "no soporta esta operación" y "falla real" en `embed()`),
aislamiento de datos por curso, TTL/invalidación del caché semántico y del CAG
Store, 5 escenarios end-to-end del Orquestador (incluida la degradación por
cola llena, QAS-P3), validación del manifest de artefactos (incluida la
whitelist de dominios para el modo *embed*), invocación de artefactos
*webhook* con fallo simulado, catálogo de cursos, `ResourceManager` (publicar
material dispara ingesta + precarga), Activity Logger/Progress
Aggregator/Dashboard, 3 pruebas de integración end-to-end con
`build_application()`, los helpers de `lambda_handler.py` que resuelven
credenciales desde Secrets Manager y ensamblan `POSTGRES_URL` (incluido su
`connect_timeout`), el timeout de pared de `ProviderRegistry` (un proveedor
que se cuelga sin respetar su propio timeout no bloquea el fallback ni
escapa del circuit breaker), y que el cliente de Secrets Manager se
configure con timeouts acotados — casos reales detectados en el PoC
desplegado, ver `GUIA_DESPLIEGUE_Y_PRUEBAS.md` sección 0.2. También cubren
las 12 rutas nuevas de gestión de cursos (FR5: crear curso, listar por
usuario, asignar docente, inscribir estudiante, publicar material — con
catálogo/estados 404/409/400 correctos), probadas con el catálogo en memoria
inyectado en `lambda_handler.py` (`PostgresCourseCatalogService` requiere
PostgreSQL real y no se ejecuta en este entorno, igual que `PGVectorStore`).

```bash
python3 -m unittest discover -s tests -v   # 95 tests, todos OK
python3 run_demo.py                        # demo narrado por consola (12 escenarios)
```

## Cómo ejecutarlo con los 3 proveedores reales, y cómo desplegarlo en AWS

Ver **`GUIA_DESPLIEGUE_Y_PRUEBAS.md`** — guía paso a paso (credenciales,
pruebas locales con `scripts/smoke_test_real_providers.py`, despliegue con
AWS SAM, y verificación del despliegue con `scripts/smoke_test_deployed_api.py`).

## Estructura

```
src/
  adapters/        LLMProviderPort + Gemini/OpenAI(gpt-oss-20b)/Llama 3.3 (generate, reales) +
                   Bedrock/Voyage (embed, respaldos, reales) + Mock (pruebas)
  registry/        Circuit breaker + Provider Registry (QAS-D1, QAS-I1)
  retrieval/       VectorStorePort + InMemory/PGVector + ingesta (Iteración 5)
  cag/             CAGStore (caché KV precargada)
  cache/           Caché semántico de respuestas (Iteración 2)
  orchestrator/    Router, Context Assembler, Orchestrator
  artifacts/       Manifest + ArtifactRegistry + ArtifactInvoker (Iteración 4)
  courses/         CourseCatalogService (en memoria) + PostgresCourseCatalogService
                   (real, FR5) + ResourceManager (Iteración 6, FR5)
  tracking/        ActivityLogger + ProgressAggregator + Dashboard (Iteración 6, FR6)
  config.py        build_orchestrator(), build_course_catalog() y
                   build_application() — ensamblaje completo
lambda_handler.py  Handler para AWS Lambda con varias rutas (POST /query,
                   y /courses/... para FR5); usa recursos reales
                   (PGVectorStore/DynamoCAGStore/PostgresCourseCatalogService)
                   si detecta las variables de entorno correspondientes, o en
                   memoria si no
scripts/           smoke_test_real_providers.py, smoke_test_deployed_api.py
                   y seed_rag_data.py (indexa material real contra el
                   despliegue en AWS para probar el escenario RAG completo)
tests/             95 pruebas con adaptadores/clientes simulados
infra/             Plantilla SAM para despliegue en AWS (ver infra/README.md)
infra/budget/      Alarma de AWS Budgets (techo de costo acordado: <USD 20 combinado AWS+IA)
GUIA_DESPLIEGUE_Y_PRUEBAS.md   Guía paso a paso de despliegue y pruebas
INSTRUCTIVO_PRUEBAS_FUNCIONALIDADES.md   Cómo probar cada funcionalidad con
                   ejemplos (incluida ingesta RAG desde texto y PDF)
```

## Qué falta para el alcance "completo"

Autenticación/autorización real contra un IdP (Iteración 7) y las rutas de
API Gateway/Lambda para **artefactos (FR3)** y **seguimiento (FR6)** —
`infra/template.yaml` ya expone `/query` y las 5 rutas de **gestión de
cursos (FR5)**, pero `ArtifactRegistry`/`ArtifactInvoker` y
`ActivityLogger`/`ProgressAggregator`/`ProgressDashboardAPI` siguen siendo
solo clases Python sin ruta HTTP ni persistencia real (el plan acordado es
Postgres para artefactos, DynamoDB para seguimiento — mismo patrón que se
usó para cursos con `PostgresCourseCatalogService`). El diseño y los puntos
de extensión ya existen (`is_enrolled_or_assigned()`, `ArtifactRegistry`),
falta conectarlos a rutas HTTP reales + adaptador persistente si se quiere
desplegar esa parte.
