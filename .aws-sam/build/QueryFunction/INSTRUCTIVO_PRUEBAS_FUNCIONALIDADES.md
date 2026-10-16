
# Instructivo de pruebas — Funcionalidades de la PoC (contra AWS desplegado)

Este instructivo prueba la PoC **usando la infraestructura real desplegada en
AWS** (API Gateway + Lambda + RDS/pgvector + DynamoDB) en todo lo que tiene
una API HTTP real. Python solo aparece en dos casos, y en ambos está
explícitamente marcado como tal:

1. **Extracción de texto de un PDF antes de indexarlo** — es un paso de
   preparación de datos, no la prueba en sí; ni la PoC ni AWS hacen parsing
   de PDF en ningún punto del pipeline, así que ese paso siempre ocurre
   fuera de la infraestructura, en tu máquina.
2. **Registro de Artefactos (FR3) y Seguimiento (FR6)** — no es una elección
   de este instructivo: el Lambda desplegado (`lambda_handler.py`) **nunca
   construye** `ArtifactRegistry`/`ArtifactInvoker`/`ProgressAggregator`/
   `ProgressDashboardAPI`, así que hoy no existe ninguna forma de probarlos
   contra AWS. Se explica por qué en la sección 7, con la alternativa
   disponible mientras esa brecha no se cierre.

Todo lo demás — el núcleo RAG/CAG (`POST /query`), la gestión de cursos
(FR5, 5 rutas) y el fallback entre proveedores — se prueba aquí
exclusivamente contra la API real. Requiere el stack ya desplegado
(`GUIA_DESPLIEGUE_Y_PRUEBAS.md`, secciones 4-6.1) y las salidas del stack a
mano:

**Nota de alcance:** este instructivo prueba funcionalidades (¿responde
correctamente esta llamada?), no atributos de calidad bajo condiciones
límite. El estado del circuit breaker y la degradación por cola llena
(QAS-P3) son preguntas de evaluación arquitectónica (ATAM Lite), no de
prueba de la PoC — ver la nota de alcance al inicio de la sección 6.

```bash
aws cloudformation describe-stacks --stack-name javeriana-ia-poc \
  --query "Stacks[0].Outputs" --output table
```

Guarda `ApiUrl` (endpoint de `/query`), `ApiBaseUrl` (base de las rutas
`/courses...`) y `LLMSecretsArn` (se usa en la sección 6). El nombre de la
función Lambda es fijo: `javeriana-ia-poc-query` (`infra/template.yaml`,
`FunctionName`).

**Las 6 rutas requieren un token de Cognito.** Antes de seguir, obtén uno
(`GUIA_DESPLIEGUE_Y_PRUEBAS.md`, sección 6.6) y expórtalo — todos los
ejemplos de este documento asumen que ya tienes `$COGNITO_ID_TOKEN`:

```bash
export COGNITO_ID_TOKEN="eyJraWQ..."   # ver GUIA sección 6.6
```

Dura 1 hora; si algún comando responde `401`, es casi siempre porque
expiró — repite el paso de la GUIA para renovarlo.

Estructura de este documento:

1. Confirmar que el stack responde
2. Gestión de cursos (FR5) vía la API real
3. Ingesta de documentos para RAG — texto y PDF — vía la API real
4. Consultas RAG/CAG vía `POST /query`
5. Camino CAG con datos reales
6. Resiliencia: forzar el fallback de un proveedor real
7. Lo que hoy NO se puede probar contra AWS (FR3/FR6) — gap real, no elección
8. Checklist final

---

## 1. Confirmar que el stack responde

```bash
python3 -m scripts.smoke_test_deployed_api <ApiUrl> SMOKE-TEST
```

Espera HTTP 200. Como `SMOKE-TEST` no tiene material indexado, la respuesta
vendrá sin RAG/CAG — eso ya confirma que todo el camino (API Gateway →
Lambda → Secrets Manager → algún proveedor LLM real) funciona antes de
seguir con el resto.

---

## 2. Gestión de cursos (FR5) vía la API real

Todo esto usa `<ApiBaseUrl>` — ver el contrato completo (parámetros,
respuestas, códigos de error) en `GUIA_DESPLIEGUE_Y_PRUEBAS.md`, sección
6.5. Aquí el flujo recomendado, en orden (el paso 3 de ingesta más abajo
requiere que el curso ya exista):

```bash
# Crear curso
curl -i -X POST "<ApiBaseUrl>/courses" -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{"courseId": "ISIS-1234", "name": "Estructuras de Datos"}'
# 201 creado · 409 si courseId ya existe · 400 si falta courseId/name · 401 si falta/expiró el token

# Asignar docente
curl -i -X POST "<ApiBaseUrl>/courses/ISIS-1234/teachers" -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{"teacherId": "prof-1"}'
# 200 · 404 si el curso no existe · 400 si falta teacherId

# Inscribir estudiante
curl -i -X POST "<ApiBaseUrl>/courses/ISIS-1234/students" -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{"studentId": "est-1"}'
# 200 · 404 · 400 (mismo patrón que teachers)

# Listar cursos de un usuario
curl -i "<ApiBaseUrl>/courses?userId=prof-1" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN"
# 200 -> {"courses": [{"courseId": "ISIS-1234", "name": "...", "teachers": [...], "students": [...]}]}
```

Prueba también los casos de error deliberadamente (crear el mismo curso dos
veces, asignar un docente a un curso inexistente, mandar el body sin
`courseId`) — cada uno debe devolver el código HTTP indicado arriba, no un
502 genérico.

---

## 3. Ingesta de documentos para RAG — texto y PDF — vía la API real

El endpoint es `POST /courses/{courseId}/material` (dispara ingesta RAG +,
opcionalmente, precarga CAG en una sola llamada). Requiere que el curso
exista (paso anterior) — internamente valida `catalog.get_course(courseId)`
antes de indexar.

### 3.1 Texto plano

```bash
curl -i -X POST "<ApiBaseUrl>/courses/ISIS-1234/material" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{
    "documentId": "syllabus-texto",
    "text": "El curso de Estructuras de Datos cubre listas, pilas, colas y árboles. Tiene 3 parciales y un proyecto final."
  }'
```

### 3.2 Archivo de texto local (`.txt`)

El contenido del archivo se envía igual, dentro del mismo JSON — usa
Python solo para no romper el escapado del texto al construir el payload
(no es una llamada al pipeline, solo arma el JSON):

```bash
python3 -c "
import json
text = open('material/syllabus.txt', encoding='utf-8').read()
print(json.dumps({'documentId': 'syllabus-txt', 'text': text}))
" > /tmp/payload.json

curl -i -X POST "<ApiBaseUrl>/courses/ISIS-1234/material" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" --data @/tmp/payload.json
```

### 3.3 PDF

**Paso previo, local, fuera de la infraestructura** (ni la PoC ni AWS
parsean PDF en ningún punto — este paso no se puede mover a la nube tal
como está construido el proyecto hoy):

```bash
pip install pypdf --break-system-packages

python3 -c "
import json
from pypdf import PdfReader
reader = PdfReader('material/syllabus.pdf')
texto = '\n'.join(page.extract_text() or '' for page in reader.pages)
print(json.dumps({'documentId': 'syllabus-pdf', 'text': texto}))
" > /tmp/payload_pdf.json
```

**Prueba real, contra AWS** — desde aquí es idéntico a 3.1/3.2:

```bash
curl -i -X POST "<ApiBaseUrl>/courses/ISIS-1234/material" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" --data @/tmp/payload_pdf.json
```

Si el PDF es escaneado (imagen, no texto seleccionable),
`page.extract_text()` devuelve cadenas vacías — necesitarías OCR antes de
este paso; no existe hoy en el proyecto.

### 3.4 Verificar que la ingesta se usó de verdad

```bash
python3 -m scripts.smoke_test_deployed_api <ApiUrl> ISIS-1234
```

Confirma dos cosas en la respuesta — no solo el HTTP 200:

1. `rag_chunks > 0`.
2. El texto de `response` refleja contenido real del documento indexado,
   no una respuesta genérica.

---

## 4. Consultas RAG/CAG vía `POST /query`

```bash
curl -i -X POST "<ApiUrl>" -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{"studentId": "est-1", "courseId": "ISIS-1234", "message": "¿cuántos parciales tiene el curso?"}'
```

La respuesta trae `source` (qué proveedor respondió), `used_cag`,
`rag_chunks` y `latency_ms` — con esto solo (sin tocar Python) puedes
verificar el comportamiento de RAG (sección 3), CAG (sección 5) y, de
forma indirecta, el fallback entre proveedores (sección 6.1).

---

## 5. Camino CAG con datos reales

Dos formas, ambas contra AWS real:

**A) Vía el mismo endpoint de material (más simple, una sola llamada):**

```bash
curl -i -X POST "<ApiBaseUrl>/courses/ISIS-1234/material" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{
    "documentId": "reglas-curso",
    "text": "El curso cubre listas, pilas y colas.",
    "cagContext": "Reglas del curso: 3 parciales, sin celulares en examen.",
    "cagTtlSeconds": 604800
  }'
```

**B) Escribiendo directo en la tabla DynamoDB** (ver GUIA sección 6.3, útil
si quieres precargar CAG sin indexar RAG a la vez):

```bash
aws dynamodb put-item --table-name javeriana-ia-cag-cache --item '{
  "course_id": {"S": "ISIS-1234"},
  "context": {"S": "Reglas del curso: 3 parciales, sin celulares en examen."},
  "version": {"N": "1"}
}'
```

En ambos casos, repite la consulta de la sección 4 y confirma
`used_cag: true`.

---

## 6. Resiliencia: forzar el fallback de un proveedor real

**Nota de alcance:** esta sección solo cubre el fallback en sí (que la
consulta responda igual aunque un proveedor falle) — es lo único de la
resiliencia que se puede verificar como una prueba de la PoC (una llamada,
una respuesta esperada). El **estado del circuit breaker**
(`CLOSED`/`OPEN`/`HALF_OPEN`) y la **degradación por cola llena** (QAS-P3)
no son preguntas de "¿funciona esta llamada?", sino de "¿el mecanismo de
disponibilidad se comporta como el diseño lo asume, bajo condiciones
reales de despliegue?" — eso es exactamente lo que evalúa ATAM Lite, no
una prueba puntual de la PoC. Los dos hallazgos correspondientes ya están
documentados en `arquitectura-add30-javeriana.md` (Iteración 3, análisis
de QAS-D1, y Iteración 2, análisis de QAS-P3) como puntos de sensibilidad
para esa evaluación.

Los adaptadores leen sus credenciales de variables de entorno que
`_load_secrets_into_env()` llena **una sola vez, al arrancar un entorno de
ejecución nuevo** (cold start) — leyendo el secreto de Secrets Manager. Por
eso, para forzar una falla real de un proveedor en el despliegue, hay que
tocar el secreto y luego forzar un cold start (cambiar el secreto solo no
alcanza: los entornos ya calientes no lo vuelven a leer).

```bash
# 1) Guarda el secreto actual (para poder revertir) y rompe solo Gemini
aws secretsmanager get-secret-value --secret-id "<LLMSecretsArn>" \
  --query SecretString --output text > /tmp/secret_original.json

python3 -c "
import json
d = json.load(open('/tmp/secret_original.json'))
d['GOOGLE_API_KEY'] = 'clave-invalida-para-prueba'
json.dump(d, open('/tmp/secret_roto.json', 'w'))
"
aws secretsmanager put-secret-value --secret-id "<LLMSecretsArn>" \
  --secret-string file:///tmp/secret_roto.json

# 2) Forzar un cold start (un cambio de configuración recicla los entornos
#    calientes — confirmado en la documentación de AWS, ver Fuentes)
FUNC=javeriana-ia-poc-query
aws lambda get-function-configuration --function-name "$FUNC" \
  --query "Environment.Variables" > /tmp/env_actual.json
python3 -c "
import json
env = json.load(open('/tmp/env_actual.json'))
env['FORCE_COLD_START'] = '1'
json.dump({'Variables': env}, open('/tmp/env_nuevo.json', 'w'))
"
aws lambda update-function-configuration --function-name "$FUNC" \
  --environment file:///tmp/env_nuevo.json

# 3) Esperar ~10-15s a que la actualización se aplique, y consultar
sleep 15
curl -s -X POST "<ApiUrl>" -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{"studentId": "est-1", "courseId": "ISIS-1234", "message": "¿qué es RAG?"}' | python3 -m json.tool
# "source" debería ser "llm:openai" (respaldo 1), no "llm:gemini"

# 4) Revertir SIEMPRE al terminar la prueba
aws secretsmanager put-secret-value --secret-id "<LLMSecretsArn>" \
  --secret-string file:///tmp/secret_original.json
aws lambda update-function-configuration --function-name "$FUNC" \
  --environment file:///tmp/env_actual.json
```

---

## 7. Lo que hoy NO se puede probar contra AWS (FR3/FR6)

No es una elección de este instructivo: revisando `lambda_handler.py`, la
función `_build_real_orchestrator()` solo construye el Orquestador (núcleo
RAG/CAG) y, por separado, `_build_course_management()` solo construye el
catálogo de cursos + `ResourceManager` (FR5). En ningún punto del código
desplegado se construyen `ArtifactRegistry`, `ArtifactInvoker`,
`ActivityLogger`, `ProgressAggregator` ni `ProgressDashboardAPI` — y
`infra/template.yaml` no define ninguna ruta HTTP para ellos. No existe
ningún evento que puedas mandarle al Lambda real para ejercitar FR3
(artefactos docentes) o FR6 (seguimiento de avance): literalmente no hay
código en el despliegue que los invoque.

Mientras esa brecha no se cierre (agregar rutas nuevas + persistencia real,
mismo esfuerzo que se hizo para FR5 con Postgres), la única forma de
probarlos es en Python, contra `build_application()` — ver la versión
anterior de este documento (conservada en el historial) o `run_demo.py`
para ejemplos completos. Si quieres que se agregue esa exposición HTTP
real para FR3/FR6 (siguiendo el mismo patrón que FR5), dilo explícitamente
y se aborda como una tarea nueva — no está incluida en este instructivo.

---

## 8. Checklist final

- [ ] Token de Cognito obtenido y exportado como `COGNITO_ID_TOKEN` (GUIA sección 6.6)
- [ ] `smoke_test_deployed_api.py` responde HTTP 200 (sección 1)
- [ ] Las 5 rutas de FR5 probadas vía `curl`, incluidos los códigos de error 404/409/400 (sección 2)
- [ ] Ingesta de texto plano, archivo `.txt` y PDF probadas vía `POST .../material`, con `rag_chunks > 0` verificado (sección 3)
- [ ] Consulta vía `POST /query` con `source`/`used_cag`/`rag_chunks` inspeccionados (sección 4)
- [ ] Camino CAG probado, con `used_cag: true` confirmado (sección 5)
- [ ] Fallback real de proveedor forzado y revertido correctamente, incluyendo restaurar el secreto original y las variables de entorno (sección 6)
- [ ] Entendido que FR3/FR6 no tienen hoy ninguna vía de prueba contra AWS (sección 7)
- [ ] Revisados los dos puntos de sensibilidad para ATAM Lite en `arquitectura-add30-javeriana.md` (observabilidad del circuit breaker en Iteración 3, y alcanzabilidad de QAS-P3 en Iteración 2) — no son parte de esta prueba, pero son insumo directo para esa evaluación

---

## Fuentes

- [UpdateFunctionConfiguration — AWS Lambda API Reference](https://docs.aws.amazon.com/lambda/latest/api/API_UpdateFunctionConfiguration.html) (un cambio de configuración recicla los entornos de ejecución calientes; la siguiente invocación es un cold start — usado en la sección 6 para forzar que el fallback lea el secreto modificado)

Los hallazgos sobre observabilidad del circuit breaker y sobre por qué la
degradación por cola llena (QAS-P3) no es alcanzable en el despliegue
serverless actual, junto con sus fuentes (documentación de concurrencia
reservada y del ciclo de vida de entornos de ejecución de Lambda), están
en `arquitectura-add30-javeriana.md` — son insumo para la evaluación ATAM
Lite, no para este instructivo.
