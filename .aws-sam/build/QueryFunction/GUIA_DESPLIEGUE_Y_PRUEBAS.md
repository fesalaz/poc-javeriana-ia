# Guía de despliegue en AWS y de pruebas — PoC Asistente Javeriana IA

Esta guía cubre, en orden: qué revisar antes de empezar (incluye un cambio
importante en la capa gratuita de AWS y en los modelos LLM disponibles), cómo
obtener credenciales de los 3 proveedores LLM (Gemini y OpenAI gpt-oss-20b/Groq,
gratuitos; Llama 3.3/OpenRouter, de pago económico — ver 0.1), cómo probar
todo localmente, cómo preparar la cuenta de AWS, cómo desplegar con AWS SAM,
cómo probar el sistema ya desplegado, cómo monitorearlo, y cómo apagarlo para
no generar costos.

No se desplegó ni se probó contra una cuenta real de AWS al construir esta
guía (el entorno donde se construyó la PoC no tiene credenciales de AWS ni
acceso a internet) — la plantilla se validó solo sintácticamente (ver
`infra/README.md`). Sigue esta guía paso a paso y valida cada sección antes de
avanzar a la siguiente.

---

## 0. Antes de empezar: la capa gratuita de AWS cambió (léelo primero)

El 15 de julio de 2025 AWS reestructuró su programa de capa gratuita. Esto
afecta directamente el supuesto de "todo gratis" de este proyecto (restricción
C5 del diseño de arquitectura), así que revisa en cuál caso estás:

- **Cuenta creada ANTES del 15 de julio de 2025 ("legacy"):** conservas el
  modelo clásico de 12 meses gratis (750 h/mes de RDS `db.t3.micro`, 20 GB de
  almacenamiento, Lambda con 1 millón de solicitudes/mes indefinidamente,
  API Gateway con 1 millón de llamadas/mes durante 12 meses).
- **Cuenta creada DESPUÉS del 15 de julio de 2025 ("Free Plan"):** en vez de
  12 meses gratis, recibes **USD 200 en créditos** (100 automáticos + 100 más
  por completar 5 tareas de onboarding: lanzar y terminar una instancia EC2,
  configurar una base RDS, desplegar una función Lambda, probar un prompt en
  Amazon Bedrock, y configurar un presupuesto en AWS Budgets). Esos créditos
  expiran a los **6 meses** o cuando se agotan, lo que ocurra primero. Cuando
  se acaban, **la cuenta se cierra automáticamente** (tienes 90 días de gracia
  para pasarte a un plan pago y recuperar tus datos antes de que se borren).
- Lambda tiene, aparte de lo anterior, un nivel **siempre gratis** (1 millón
  de solicitudes y 400 000 GB-segundo/mes) que aplica sin importar la fecha de
  creación de la cuenta.

**Recomendación concreta:** sin importar en qué caso estés, configura una
alarma de facturación (AWS Budgets) antes de desplegar nada — es, además, una
de las tareas que desbloquea créditos extra si tu cuenta es nueva. Ver el
paso 4.2.

### 0.1 Los modelos LLM gratuitos también cambiaron (importante)

El diseño de arquitectura fijaba originalmente como restricción (C2) usar
"versiones gratuitas de Google AI Studio (Gemini) y Meta Llama 3.1". Al
construir esta guía (agosto de 2026) verificamos el estado de esos modelos,
encontramos una deprecación, y **terminamos ampliando el Provider Registry a
3 proveedores** en vez de solo 2 — esto ya está reflejado en el código
(`.env.example`, `infra/template.yaml`, `src/adapters/`, `src/config.py`).
De los 3, **2 son gratuitos** (Gemini y Groq/`gpt-oss-20b`) y **1 es de pago
económico** (Llama 3.3 vía OpenRouter — su slug `:free` dejó de estar
disponible en la práctica, ver punto 3 abajo):

1. **Gemini** (primario, también resuelve embeddings): `gemini-2.5-flash`
   tiene fecha de apagado el 16 de octubre de 2026. El proyecto ya usa por
   defecto `gemini-3.5-flash` (estable/GA), el reemplazo recomendado por
   Google. No se aparta de C2.
   - **Su modelo de embeddings también cambió:** `models/text-embedding-004`
     (el que usaba este proyecto) se apagó el **14 de enero de 2026**. El
     reemplazo estable es `gemini-embedding-001`, ya configurado por defecto
     (`GEMINI_EMBEDDING_MODEL`, ver `.env.example`/`infra/template.yaml`).
     Si ves `AllProvidersFailedError` al llamar `registry.embed(...)` con el
     detalle de un error de Gemini (no de Groq/OpenRouter, que nunca
     soportan embeddings por diseño — ver 3.3), revisa primero este modelo.
2. **OpenAI `gpt-oss-20b` vía Groq** (respaldo 1): Groq **deprecó
   `llama-3.1-8b-instant` el 16 de agosto de 2026** y su reemplazo oficial
   recomendado es `openai/gpt-oss-20b` — un modelo de pesos abiertos de
   OpenAI, no de Meta. Se mantiene como proveedor de respaldo por su baja
   latencia en el tier gratuito de Groq, pero ya no se le llama "Llama": tiene
   su propia clase (`OpenAIAdapter`, `src/adapters/openai_adapter.py`).
3. **Meta Llama 3.3 vía OpenRouter** (respaldo 2): para no perder un Llama
   real en el diseño, se investigaron alternativas — Cerebras no ofrece
   ningún modelo Llama en su tier gratuito; Together AI sí ofrece Llama 3.3
   70B pero de pago (~USD 1.04/1M tokens). Se intentó `meta-llama/llama-3.3-70b-instruct:free`
   de OpenRouter (131K de contexto, catalogado como gratuito), pero en la
   práctica no funcionó, en dos pasos:
   - Primero respondió `404 - No endpoints found matching your data policy`:
     causado por los toggles de datos en Settings → Privacy de la cuenta
     ("Free endpoints that may train on request data" / "...may publish
     prompts") — se solucionó habilitándolos.
   - Después de habilitarlos, respondió un 404 **distinto y explícito**:
     `"This model is unavailable for free. The paid version is available
     now - use this slug instead: meta-llama/llama-3.3-70b-instruct"`. Es
     decir, el slug `:free` de este modelo específico ya no está disponible
     — no es un tema de configuración de la cuenta, es que OpenRouter dejó
     de ofrecerlo gratis (aunque su página de catálogo todavía lo muestre
     como "Free"; el tier gratuito de OpenRouter cambia de disponibilidad
     con frecuencia, sin previo aviso).

   Por eso el proyecto usa por defecto la variante de **pago**
   `meta-llama/llama-3.3-70b-instruct` (sin `:free`): sigue siendo Llama 3.3
   real de Meta (desviación de versión frente a Llama 3.1, ya aceptada),
   pero deja de ser "tier gratuito" — **segunda desviación** de las
   restricciones C2/C5, con un costo real pero pequeño para un PoC (~USD
   0.10/1M tokens de entrada, USD 0.32/1M de salida, proveedor más barato).
   Si OpenRouter vuelve a ofrecer un `:free` funcional en el futuro, es
   cambio de solo configuración (`LLAMA_MODEL`), ver sección 2.3.

En resumen: el PoC pasó de un diseño "Gemini + Llama" a "Gemini + OpenAI +
Llama" — 2 de los 3 gratuitos, 1 de pago económico — con `ProviderRegistry`
haciendo fallback en cadena entre los tres (QAS-D1). Agregar, quitar, o
cambiar cualquiera de los tres (por ejemplo, volver a `:free` si OpenRouter
lo reactiva) sigue siendo solo configuración (QAS-I1) — no requiere tocar el
Orquestador.

### 0.2 Timeouts en cadena: por qué una consulta puede tardar más de lo esperado

Con 3 proveedores en fallback, si uno se queda "colgado" (red lenta,
throttling) sin un tope explícito, puede consumir todo el tiempo disponible
antes de que el circuit breaker tenga oportunidad de conmutar al siguiente —
esto fue la causa real de `ReadTimeout` al probar la API ya desplegada.

**Primer intento (insuficiente):** se fijó `timeout=8s` y `max_retries=1` en
los 3 adaptadores (`ChatGoogleGenerativeAI`, `ChatOpenAI` x2) y se alineó el
`Timeout` de la función Lambda a 28s (justo por debajo del tope fijo de 29s
de integración de API Gateway REST). Al revisar CloudWatch Logs tras
desplegar este cambio, **se confirmó que no fue suficiente**: 3 invocaciones
seguidas mostraron `Status: timeout` con `Duration: 28000.00 ms` exactos —
es decir, la llamada a Gemini (`ChatGoogleGenerativeAI`) se quedó colgada
hasta que el propio Timeout de Lambda mató la ejecución, sin que el
`timeout=8s` del adaptador la cortara antes y sin que el circuit breaker
llegara a conmutar a los otros 2 proveedores. Esto coincide con un issue
abierto en `langchain-google-genai` (ver Fuentes): el parámetro `timeout` de
`ChatGoogleGenerativeAI` no siempre se respeta de forma estricta.

**Fix real (aplicado):** el tope de tiempo ya no depende de que cada SDK de
proveedor respete su propio `timeout`. `ProviderRegistry._dispatch()`
(`src/registry/provider_registry.py`) ejecuta cada llamada a
`generate()`/`embed()` en un hilo aparte y **deja de esperarla** tras
`LLM_REQUEST_TIMEOUT_SECONDS` segundos (default 6, bajado de 8 para dejar
margen: hasta 4 intentos secuenciales en el peor caso por consulta — 1 de
`embed()` + 3 de `generate()` — caben en los 28s de Lambda con ~4s de
margen). Si el proveedor sigue sin responder, se cuenta como falla real
contra su circuit breaker y el Provider Registry pasa al siguiente
inmediatamente, sin esperar. El `timeout=` de cada adaptador se deja como
defensa adicional (por si el SDK lo llega a respetar en una versión futura),
no como el mecanismo principal. Cubierto por pruebas en
`tests/test_provider_registry.py` (`test_wall_clock_timeout_*`) con un doble
de prueba que simula el colgado real.

Limitación aceptada para el PoC: en Python no se puede cancelar de forma
forzosa un hilo bloqueado en I/O de red, así que "dejar de esperar" no es lo
mismo que "cancelar" — el hilo huérfano sigue corriendo en segundo plano
hasta que la llamada de red subyacente termine o el entorno de Lambda se
recicle. Para producción a mayor escala se preferiría un cliente HTTP con
cancelación real (p.ej. `httpx` con timeouts de conexión/lectura).

**Tercer hallazgo (`scripts/smoke_test_real_providers.py` en local, antes de
desplegar):** al bajar `LLM_REQUEST_TIMEOUT_SECONDS` de 8 a 6, Gemini empezó
a fallar de inmediato en `health_check()` con:

```
400 INVALID_ARGUMENT: {'error': {'code': 400, 'message': 'Manually set
deadline 6s is too short. Minimum allowed deadline is 10s.', ...}}
```

Este es un error distinto a los dos anteriores: no es un colgado, es la API
de Google **rechazando de inmediato** cualquier deadline menor a 10 segundos
— un piso duro de la API, no una recomendación que LangChain pueda ignorar.
Como `LLM_REQUEST_TIMEOUT_SECONDS=6` es compartido por los 3 adaptadores (y
por el timeout de pared de `ProviderRegistry`), no se puede simplemente subir
a 10s para todos: eso rompería el presupuesto de tiempo del Lambda (10s x
hasta 4 intentos secuenciales = 40s, más que los 28s disponibles). La
solución fue **desacoplar los dos usos** del valor: `gemini_adapter.py` ahora
aplica `max(LLM_REQUEST_TIMEOUT_SECONDS, 10.0)` solo para el parámetro
`timeout=` que le pasa al SDK de Google (para cumplir su piso mínimo), mientras
que `ProviderRegistry` sigue usando el valor sin recortar (6s) para decidir
cuándo dejar de esperar a este adaptador — son mecanismos independientes: uno
es un parámetro que la API de Google exige y valida; el otro es que nuestro
propio código deja de esperar un hilo, sin que le importe qué deadline cree
Gemini que tiene.

Además, el timeout del cliente HTTP en `scripts/smoke_test_deployed_api.py`
se subió a 45s por defecto (antes 15s): la primera invocación (cold start)
importa LangChain/boto3/psycopg y abre conexión a RDS y Secrets Manager
antes de llamar a los proveedores LLM, así que puede tardar más que
invocaciones posteriores (ya con el entorno de ejecución "caliente").

**Cuarto hallazgo — el hang no siempre está en la cadena de proveedores
LLM:** tras aplicar el timeout de pared de `ProviderRegistry`, CloudWatch
Logs siguió mostrando `Status: timeout` a los 28000ms exactos, pero esta vez
**sin ningún log intermedio** — ni siquiera el warning de Gemini que sí
aparecía en el segundo hallazgo. Eso significa que el colgado ocurre *antes*
de llamar a cualquier proveedor: durante `_build_real_orchestrator()`
(`lambda_handler.py`), específicamente al construir `PGVectorStore` — la
clase `PGVector` de `langchain_postgres` suele abrir una conexión real a
Postgres (y validar/crear su esquema) al construirse, y esa conexión no
tenía ningún `connect_timeout`. Como `_build_real_orchestrator()` se cachea
en `_orchestrator` solo si termina de construirse, un colgado ahí hace que
CADA invocación (fría o caliente) lo reintente desde cero — explica por qué
se veía el mismo colgado exacto en invocaciones consecutivas, incluida una
sin `INIT_START` (entorno ya "caliente").

El timeout de pared de `ProviderRegistry` NO cubre este paso porque ocurre
antes de que exista un `ProviderRegistry` con el que hacer fallback. La
corrección fue agregar timeouts explícitos a **todo** paso de red que ocurre
antes de la cadena de proveedores:

- `_build_postgres_url()` (`lambda_handler.py`) agrega `?connect_timeout=5`
  (configurable con `POSTGRES_CONNECT_TIMEOUT_SECONDS`) a la URL de Postgres
  que ensambla para el despliegue real en AWS.
- El cliente de Secrets Manager (`_load_secrets_into_env()`) y el de
  DynamoDB (`DynamoCAGStore`) ahora fijan `connect_timeout=5`/`read_timeout=8`
  explícitos vía `botocore.config.Config`, en vez de los defaults de
  botocore (60s), como defensa adicional.

Con esto, si RDS/Secrets Manager/DynamoDB fallan o son inalcanzables, el
error llega en segundos (no en los 28s completos del Lambda) y queda
registrado (Secrets Manager ya lo hacía; ahora también con un timeout
acotado).

**Quinto hallazgo — el mismo colgado exacto persistió incluso después del
fix anterior:** un despliegue posterior con los `connect_timeout` ya
aplicados mostró en CloudWatch Logs el mismo patrón, otra vez sin ningún log
intermedio. Esto reveló un problema más de fondo: cuando Lambda mata una
invocación por `Timeout`, no queda ningún traceback ni pista de en qué línea
se quedó — a diferencia de una excepción real, que sí se puede atrapar y
loguear. Sin instrumentación, cada ronda de "aplicar un timeout más" es
básicamente adivinar a ciegas.

Por eso `lambda_handler.py` ahora imprime un **checkpoint con el tiempo
transcurrido en cada etapa** de `_build_real_orchestrator()` (secretos
cargados, `POSTGRES_URL` ensamblado, provider registry listo, construcción
de `PGVectorStore`/`DynamoCAGStore`, orquestador ensamblado) y de
`handle_query()`. Esto es deliberadamente permanente, no código de depuración
a quitar después: la próxima vez que algo se cuelgue, el último checkpoint
impreso en CloudWatch va a decir exactamente en qué línea se quedó, en vez de
tener que seguir adivinando. También se corrigió un bug relacionado:
`handler()` no envolvía la construcción del orquestador (`_get_orchestrator()`)
en ningún `try/except` — solo el `handle_query()` posterior lo tenía — así
que si esa construcción fallaba (en vez de colgarse) con una excepción real,
el Lambda terminaba en un crash sin ninguna respuesta HTTP clara. Ahora
devuelve un 502 con el mensaje real del error.

**Qué hacer con el próximo despliegue:** después de `sam deploy`, prueba de
nuevo y revisa CloudWatch Logs — ahora deberías ver una secuencia de líneas
`[orchestrator-init] ...` (o `[handle_query] ...`) antes de cualquier
`Status: timeout`. La última línea impresa antes del corte indica exactamente
dónde seguir investigando (por ejemplo, si el último checkpoint es
"construyendo PGVectorStore (conecta a RDS)..." sin el checkpoint siguiente,
el problema es realmente conectividad a RDS, no un timeout mal configurado
en el código).

**Sexto hallazgo — los checkpoints sí revelaron algo útil esta vez:** dos
invocaciones (una fría, una en un entorno reutilizado) mostraron el mismo
patrón: `_load_secrets_into_env()` tarda ~2.4s (razonable, llamada real a
Secrets Manager), pero **`build_provider_registry()` por sí sola tarda
~18-20s** — antes de siquiera intentar conectarse a RDS. Es decir, el cuello
de botella real no era Postgres ni ningún proveedor LLM: es construir los 3
adaptadores (que internamente importan `langchain-google-genai` y
`langchain-openai`, y con ellos sus dependencias transitivas — grpc,
protobuf, el SDK de OpenAI, httpx). Estas librerías tienen imports
notoriamente pesados, y el `QueryFunction` estaba configurado con
`MemorySize: 512` — en AWS Lambda la CPU asignada escala de forma lineal con
la memoria hasta 1792 MB (donde se llega a 1 vCPU completo); con 512 MB el
Lambda solo tiene ~0.29 vCPU, lo que puede hacer que un import pesado tarde
varias veces más que en una laptop de desarrollo.

Se agregaron checkpoints más finos **dentro** de `build_provider_registry()`
(`src/config.py`), uno antes/después del import y de la construcción de cada
adaptador (Gemini, OpenAI/Groq, Llama/OpenRouter), para confirmar si el
costo está concentrado en un import específico o repartido entre los tres.
También se subió `MemorySize` de 512 a 1024 MB en `infra/template.yaml` —
duplica la CPU disponible durante ese import, con impacto mínimo en el techo
de costo del proyecto (la facturación es memoria × duración: si la duración
baja proporcionalmente, el costo total no debería subir de forma
significativa).

**Confirmado con el siguiente despliegue:** con `MemorySize: 1024`,
`build_provider_registry()` bajó de ~18-20s a **~9s** (import de Gemini
3.6s + construcción 0.2s; import de OpenAI/Groq 2.5s + construcción 1.6s;
Llama reutiliza el import de `langchain-openai` ya cargado, ~0s) — la mitad,
consistente con el doble de CPU asignado. La invocación completa terminó en
`Duration: 15603.90 ms`, muy por debajo de los 28000ms de Timeout (`PGVectorStore`
se conectó en 3.5s, `DynamoCAGStore` fue instantáneo, y `handle_query()`
respondió en 2.99s). No hubo `Status: timeout`. Con esto, la cadena completa
—secretos, Postgres, los 3 adaptadores, y una consulta real end-to-end—
funciona dentro del presupuesto de tiempo del Lambda. Si en el futuro vuelve
a acercarse al límite (por ejemplo, si se agregan más proveedores o
dependencias), el siguiente paso sería subir a ~1792 MB (1 vCPU completo) o
mover las dependencias pesadas a una Lambda Layer.

---

## 1. Requisitos previos (software a instalar)

| Herramienta | Para qué | Verificar con |
|---|---|---|
| Cuenta de AWS | Desplegar la infraestructura | — |
| [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) | Configurar credenciales, inspeccionar recursos | `aws --version` |
| [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) | Construir y desplegar `infra/template.yaml` | `sam --version` |
| Python 3.11+ | Ejecutar pruebas, el demo y los scripts de esta guía | `python3 --version` |
| Docker (Desktop o equivalente, corriendo) | PostgreSQL+pgvector local, **y obligatorio para `sam build --use-container`** (ver 5.2) | `docker --version` |
| Cliente `psql` (o DBeaver/pgAdmin) | Habilitar la extensión `pgvector` en RDS | `psql --version` |
| Cuenta de [Google AI Studio](https://aistudio.google.com/) | API key gratuita de Gemini (proveedor 1) | — |
| Cuenta de [Groq](https://console.groq.com/) | API key gratuita de `openai/gpt-oss-20b` (proveedor 2, ver 0.1) | — |
| Cuenta de [OpenRouter](https://openrouter.ai/), con créditos cargados | API key de Meta Llama 3.3, modelo de pago económico (proveedor 3, ver 0.1) | — |

---

## 2. Obtener credenciales de los proveedores LLM (generate() + embed())

### 2.1 Google AI Studio (Gemini) — proveedor 1 (primario)

1. Entra a [aistudio.google.com](https://aistudio.google.com/) con una cuenta
   de Google y acepta los términos.
2. Haz clic en **"Create API key"**. Solo pide verificación de la cuenta
   (teléfono o correo de recuperación), no tarjeta de crédito.
3. Guarda la clave — la necesitarás como `GOOGLE_API_KEY`.

Ten en cuenta (2026): el nivel gratuito solo cubre los modelos **Flash**
(los modelos Pro requieren facturación). Este proyecto usa por defecto
`gemini-3.5-flash` (ver `.env.example` e `infra/template.yaml`); revisa tus
límites vigentes en [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit),
porque cambian por cuenta y con el tiempo. Google también anunció que a
partir de septiembre de 2026 dejará de aceptar API keys "Standard" antiguas —
si tu clave es de ese tipo, créala de nuevo antes de esa fecha.

### 2.2 Groq — proveedor 2, `openai/gpt-oss-20b` (respaldo 1 — ver 0.1)

1. Entra a [console.groq.com](https://console.groq.com/) y crea una cuenta
   (no pide tarjeta de crédito).
2. Ve a **API Keys** → **Create API Key**.
3. Guarda la clave — la necesitarás como `OPENAI_OSS_API_KEY`. El modelo por
   defecto de este proyecto es `openai/gpt-oss-20b` (Llama 3.1 ya no está
   disponible en el nivel gratuito de Groq, ver 0.1).

Límites vigentes del nivel gratuito de Groq (2026): 30 solicitudes/minuto,
6 000 tokens/minuto, 14 400 solicitudes/día — más que suficiente para probar
esta PoC. Revisa [console.groq.com/docs/deprecations](https://console.groq.com/docs/deprecations)
antes de desplegar, por si el modelo vuelve a cambiar.

### 2.3 OpenRouter — proveedor 3, Meta Llama 3.3 (respaldo 2 — ver 0.1)

1. Entra a [openrouter.ai](https://openrouter.ai/) y crea una cuenta.
2. Ve a **Keys** → **Create Key**.
3. Guarda la clave — la necesitarás como `LLAMA_API_KEY`. El modelo por
   defecto de este proyecto es `meta-llama/llama-3.3-70b-instruct` (de pago,
   ver 0.1) — necesitas cargar créditos: **Settings → Credits** (unos pocos
   dólares alcanzan de sobra para una PoC; el costo real es de centavos).

**¿Por qué no usamos la variante `:free`?** Se intentó, pero no funciona en
la práctica: primero falló por los toggles de datos en Settings → Privacy
("Free endpoints that may train on request data" / "...may publish
prompts"); tras habilitarlos, OpenRouter respondió un 404 explícito
diciendo que el slug `:free` de este modelo específico ya no está
disponible y que hay que usar la versión de pago — aunque la página de
catálogo del modelo todavía lo muestre como "Free". El tier gratuito de
OpenRouter cambia de disponibilidad con frecuencia y sin previo aviso, así
que no es algo que se pueda garantizar como parte fija del diseño. Si en tu
caso `:free` sí responde (puede que OpenRouter lo reactive, o que corra con
otro modelo `:free` distinto), cambia `LLAMA_MODEL` en tu `.env` — es solo
configuración, ver 0.1.

Si decides intentar con `:free` de todos modos, revisa primero
[openrouter.ai/models](https://openrouter.ai/models?max_price=0) para ver
qué modelos `:free` están vigentes hoy, ya que esta lista cambia.

### 2.4 AWS Bedrock — respaldo 1 de `embed()` (agregado septiembre 2026)

**No requiere crear ninguna credencial nueva.** `BedrockEmbeddingAdapter` usa
las credenciales del rol de ejecución de `QueryFunction` (IAM), ya concedidas
por `infra/template.yaml`. El único paso manual, una sola vez por cuenta/
región, es habilitar el acceso al modelo en la consola:

1. AWS Console → **Amazon Bedrock** → **Model access** (en el menú lateral).
2. Busca **Titan Text Embeddings V2** (proveedor Amazon) y haz clic en
   **Enable** / **Request model access** — para modelos de Amazon suele
   aprobarse de inmediato, sin formulario de caso de uso.
3. Confirma que quedó en estado **Access granted** antes de desplegar o
   invocar la API — si no, `BedrockEmbeddingAdapter` fallará con
   `AccessDeniedException` (una falla real de proveedor, distinta de un
   error de permisos IAM mal configurados).

Por qué se agregó: bajo la prueba de carga real de 100 usuarios concurrentes,
solo Gemini implementaba `embed()` entre los 3 proveedores originales — al
abrirse su circuit breaker, todo el pipeline RAG quedó sin ningún proveedor
alterno al que conmutar (hallazgo R-7, ver `EVALUACION_ATAM_LITE.md`).

### 2.5 Voyage AI — respaldo 2 de `embed()` (agregado septiembre 2026)

1. Entra a [dash.voyageai.com](https://dash.voyageai.com/) y crea una cuenta.
2. Ve a **API Keys** → crea una clave nueva.
3. Guarda la clave — la necesitarás como `VOYAGE_API_KEY`. El modelo por
   defecto de este proyecto es `voyage-4-lite`.

Cada cuenta recibe 200 millones de tokens gratis (generación voyage-4) — de
sobra para probar esta PoC, incluida una prueba de carga completa de 100
usuarios concurrentes, sin gastar nada. Se eligió como segundo respaldo (y no
un segundo modelo de Bedrock) por diversidad real de proveedor: si AWS
Bedrock tuviera una degradación propia, Voyage es infraestructura
completamente independiente.

---

## 3. Probar todo localmente ANTES de tocar AWS

No despliegues nada en AWS hasta que esta sección pase sin errores — es
mucho más fácil depurar credenciales y lógica en tu máquina que en Lambda.

### 3.1 Pruebas sin credenciales (lo que ya viene verificado)

```bash
cd poc-javeriana-ia
python3 -m unittest discover -s tests -v   # 91 pruebas, no requieren red ni credenciales
python3 run_demo.py                        # demo de 12 escenarios, ídem
```

### 3.2 Instalar las dependencias reales

```bash
pip install -r requirements.txt
cp .env.example .env
```

Edita `.env` y completa `GOOGLE_API_KEY`, `OPENAI_OSS_API_KEY` y
`LLAMA_API_KEY` (paso 2). Deja `USE_REAL_LLM_PROVIDERS=true`.

### 3.3 Probar los proveedores LLM reales (sin AWS todavía)

```bash
python3 -m scripts.smoke_test_real_providers
```

Este script llama a los 3 proveedores (Gemini, `openai/gpt-oss-20b` vía Groq,
Llama 3.3 vía OpenRouter — ver 0.1) uno por uno, y también prueba el
fallback automático en cadena del `ProviderRegistry`. Si falla aquí, no vale
la pena seguir — revisa las claves antes de continuar.

### 3.4 Probar PostgreSQL + pgvector local

**No necesitas crear ninguna base de datos ni usuario a mano.**
`docker-compose.yml` ya define `POSTGRES_DB: javeriana_ia` y
`POSTGRES_USER`/`POSTGRES_PASSWORD: postgres` como variables de entorno de la
imagen oficial de Postgres — esa imagen crea automáticamente esa base y ese
usuario la primera vez que el contenedor arranca (no antes, no después: si ya
tenías el volumen `pgdata` de una corrida previa con otro nombre de BD/usuario,
esas variables se ignoran; borra el volumen con `docker compose down -v` si
necesitas que se re-cree desde cero).

```bash
docker compose up -d
psql "postgresql://postgres:postgres@localhost:5432/javeriana_ia" \
     -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

El único paso manual es ese `CREATE EXTENSION` — habilita `pgvector` dentro de
la base que la imagen ya creó (ninguna imagen de Postgres trae extensiones
habilitadas por defecto, aunque el binario esté instalado).

Agrega `POSTGRES_URL=postgresql+psycopg://postgres:postgres@localhost:5432/javeriana_ia`
a tu `.env` y vuelve a correr `python3 -m scripts.smoke_test_real_providers`:
ahora también debería probar la ingesta y la búsqueda vectorial reales.

---

## 4. Preparar la cuenta de AWS

### 4.1 Configurar credenciales de AWS CLI

```bash
aws configure
# AWS Access Key ID, Secret Access Key, región (p.ej. us-east-1), formato: json
```

Para una PoC académica es razonable usar un usuario IAM con permisos amplios
(`AdministratorAccess`) para simplificar; si prefieres permisos acotados,
necesitarás al menos: CloudFormation, Lambda, API Gateway, RDS, DynamoDB,
Cognito, S3, Secrets Manager, CloudWatch, EC2 (para leer VPC/subredes/SG) e
IAM (para que SAM cree roles de ejecución).

### 4.2 Techo de costo del proyecto (USD 20) y alarma de presupuesto

El equipo del proyecto fijó un **techo de costo combinado de menos de USD 20**
para esta PoC — AWS más la ejecución de los modelos de IA, en caso de que
llegue a ser necesario. Antes de desplegar, conviene entender cómo se
reparte ese techo entre los distintos componentes, porque no todos se
comportan igual frente al costo:

| Componente | Modelo de facturación | Riesgo real de exceder el techo |
|---|---|---|
| Gemini (Google AI Studio) | Tier gratuito, sin facturación habilitada | Ninguno — al llegar al límite de tasa simplemente responde 429, no cobra |
| `openai/gpt-oss-20b` vía Groq | Tier gratuito, sin facturación habilitada | Ninguno, por la misma razón |
| Llama 3.3 vía OpenRouter | **Prepago** (créditos que tú cargas) | Ninguno más allá de lo que cargues — no puede cobrarte más de tus créditos disponibles; con ~USD 0.10/0.32 por 1M tokens, incluso USD 3-5 alcanzan de sobra para una PoC |
| AWS Bedrock (Titan Embeddings V2, respaldo 1 de `embed()`) | **Pospago**, pero extremadamente barato | $0.02 por 1M tokens de entrada — una prueba de carga completa de 100 usuarios concurrentes cuesta centavos, no dólares (ver estimación en `EVALUACION_ATAM_LITE.md`) |
| Voyage AI (respaldo 2 de `embed()`) | **Prepago con regalo gratuito** | 200 millones de tokens gratis por cuenta — en la práctica, USD 0 para cualquier prueba de esta PoC |
| AWS (RDS, Lambda, API Gateway, DynamoDB, Cognito, S3) | **Pospago** — se factura después de usarse, sin tope automático | **Es el único riesgo real.** Estimaciones públicas (ver Fuentes) sitúan una instancia `db.t3.micro` de PostgreSQL corriendo un mes completo, sin capa gratuita, en ~USD 12–22/mes solo de cómputo, más ~USD 2–3/mes de almacenamiento (20 GB gp2); el resto de servicios (Lambda, API Gateway, DynamoDB, Cognito, S3, Bedrock) es negligible al tráfico bajo de una PoC. Si tienes la capa gratuita legacy (750 h/mes de RDS) o créditos del Free Plan vigentes (sección 0), el costo real puede ser cercano a USD 0 — pero no hay que asumirlo sin confirmarlo. |

**Conclusión práctica:** el riesgo de superar los USD 20 está casi
enteramente en el lado de AWS, y crece con el tiempo que dejes la instancia
RDS corriendo — no con cuántas consultas hagas al asistente. Por eso:

1. **Carga solo unos pocos dólares de crédito en OpenRouter** (Settings →
   Credits) — con eso ya queda acotado ese lado, sin depender de alarmas.
2. **Crea una alarma de presupuesto en AWS** antes de desplegar nada. Este
   proyecto incluye plantillas listas para crearla por CLI en
   `infra/budget/` (ver `infra/budget/README.md` para el paso a paso) — un
   presupuesto mensual de USD 15 con alertas al 50%, 80% y pronóstico ≥100%,
   dejando margen dentro del techo de USD 20 junto con los créditos de
   OpenRouter. También puedes crearla manualmente: AWS Console → **Billing
   and Cost Management** → **Budgets** → **Create budget**. Configurar una
   alarma cuenta además como una de las 5 tareas que desbloquean créditos
   extra si tu cuenta es nueva (ver sección 0).
3. **La alarma solo avisa, no detiene nada.** La mitigación real es correr
   `sam delete` (sección 9) en cuanto termines de probar — es la acción que
   más impacta el costo, porque RDS cobra por hora corrida, no por uso.

### 4.3 Ubicar tu VPC y subredes

La plantilla necesita una VPC y al menos 2 subredes en distintas zonas de
disponibilidad (requisito de RDS, aunque el PoC use una sola instancia sin
Multi-AZ):

```bash
aws ec2 describe-vpcs --filters Name=is-default,Values=true \
  --query "Vpcs[0].VpcId" --output text

aws ec2 describe-subnets --filters Name=default-for-az,Values=true \
  --query "Subnets[].SubnetId" --output text
```

Anota el `VpcId` y al menos 2 `SubnetId` — los pedirá `sam deploy --guided`.

### 4.4 Instalar y verificar AWS SAM CLI

Sigue la [guía oficial de instalación](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
según tu sistema operativo, luego:

```bash
sam --version
```

---

## 5. Desplegar con AWS SAM

### 5.1 Validar la plantilla

```bash
sam validate --template infra/template.yaml
```

### 5.2 Construir (instala `requirements.txt` automáticamente)

Ejecuta desde la **raíz del proyecto** (SAM resuelve `CodeUri: ../` en la
plantilla relativo a la ubicación del archivo, por eso apuntamos a
`infra/template.yaml` pero seguimos parados en la raíz):

```bash
sam build -t infra/template.yaml --use-container
```

**Usa siempre `--use-container` en este proyecto, no solo si el paquete se
acerca al límite de tamaño.** El motivo es más importante que el tamaño:
`requirements.txt` incluye dependencias con partes compiladas (`psycopg[binary]`,
y transitivamente algunas de LangChain) — si construyes SIN contenedor, SAM
instala esas dependencias usando tu Python e intérprete **local** (macOS o
Windows), lo que descarga binarios para *tu* sistema operativo, no para
Amazon Linux (el que corre Lambda). El paquete se sube sin errores, pero el
Lambda falla en tiempo de ejecución con `Runtime.ImportModuleError` porque
esos binarios no son compatibles. `--use-container` construye dentro de una
imagen Docker que replica el entorno real de Lambda (Amazon Linux + el
Python del runtime declarado en la plantilla, ver `Runtime: python3.13`), así
que evita ese problema y de paso resuelve un error común al construir sin
contenedor:

```
Error: PythonPipBuilder:Validation - Binary validation failed for python,
searched for python in following locations [...] which did not satisfy
constraints for runtime: python3.11. Do you have python for runtime:
python3.11 on your PATH?
```

Este error aparece cuando tu máquina no tiene instalada, en el PATH, la
misma versión de Python que dice `Runtime` en la plantilla (por ejemplo, si
tienes Python 3.13 pero la plantilla pedía `python3.11`) — SAM, sin
contenedor, busca ese binario exacto para instalar las dependencias con él.
Este proyecto ya usa `Runtime: python3.13` (LTS, soportada por Lambda hasta
octubre de 2029) precisamente para reducir este tipo de desajuste, pero la
única forma de eliminarlo del todo — y la única que evita el problema de
binarios de arriba — es `--use-container`.

Requiere tener **Docker Desktop (o equivalente) instalado y corriendo**
localmente; la primera vez que lo uses, SAM descarga la imagen base de
Lambda para Python (puede tardar unos minutos).

Si aun así el paquete final se acerca al límite de tamaño de Lambda (250 MB
descomprimido, por las dependencias de LangChain), considera separar
dependencias pesadas en una Lambda Layer.

### 5.3 Desplegar (modo guiado, primera vez)

```bash
sam deploy --guided
```

Te pedirá, entre otras cosas:

| Prompt | Qué responder |
|---|---|
| Stack Name | p.ej. `javeriana-ia-poc` |
| AWS Region | la misma que usaste en `aws configure` |
| Confirm changes before deploy | `Y` (revisa el plan antes de aplicar) |
| Allow SAM CLI IAM role creation | `Y` (necesario: crea el rol de ejecución del Lambda) |
| Parameter `VpcId` | el que obtuviste en 4.3 |
| Parameter `SubnetIds` | los 2+ subnet IDs de 4.3, separados por coma |
| Parameter `DBUsername` / `DBPassword` | credenciales para PostgreSQL |
| Parameter `GoogleApiKey` / `OpenAiOssApiKey` / `LlamaApiKey` | las de la sección 2 (proveedores 1, 2 y 3) |
| Parameter `DBAllowedCidr` | déjalo en `0.0.0.0/0` solo para este PoC académico temporal (ver `infra/README.md`, punto 2, para el trade-off), o restringe a tu IP con `curl ifconfig.me` + `/32` |
| Save arguments to configuration file | `Y` (crea `samconfig.toml`, así no vuelves a escribir todo esto) |

La creación de la instancia RDS toma entre 10 y 15 minutos; el resto (Lambda,
API Gateway, DynamoDB, Cognito, Secrets Manager) es casi inmediato.

### 5.4 Obtener las salidas del stack

```bash
aws cloudformation describe-stacks --stack-name javeriana-ia-poc \
  --query "Stacks[0].Outputs" --output table
```

Guarda especialmente `ApiUrl`, `PostgresEndpoint`, y `UserPoolId`/
`UserPoolClientId` (los necesitas en la sección 6.6 para obtener un token
de Cognito — la API no responde nada sin uno).

---

## 6. Pasos manuales después del primer despliegue

### 6.1 Habilitar pgvector en RDS

**No hay que crear la base de datos ni el usuario de PostgreSQL a mano** —
CloudFormation ya lo hizo al provisionar `PostgresDB`: la instancia se crea
con `DBName: javeriana_ia` (fijo en la plantilla) y con `MasterUsername`/
`MasterUserPassword` iguales a los parámetros `DBUsername`/`DBPassword` que
escribiste en `sam deploy --guided` (sección 5.3) — RDS crea esa base y ese
usuario maestro automáticamente al aprovisionar la instancia.

Lo único que CloudFormation NO puede hacer es ejecutar SQL *dentro* de la
base ya creada, así que la extensión `pgvector` sí requiere un paso manual,
una sola vez:

```bash
psql "postgresql://<DBUsername>:<DBPassword>@<PostgresEndpoint>:5432/javeriana_ia" \
     -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Usa el mismo `DBUsername`/`DBPassword` que escribiste en `sam deploy --guided`
(no "postgres"/"postgres" del docker-compose local) y el `PostgresEndpoint`
exacto de las salidas del stack (sección 5.4). Si la conexión falla, revisa
que tu IP esté permitida en `DBAllowedCidr` (sección 5.3).

**Nota sobre cómo se conecta el Lambda (sin que tengas que hacer nada más):**
la contraseña que escribiste no se pasa al Lambda como variable de entorno en
texto plano — vive en el secreto `LLMSecrets` (Secrets Manager), y
`lambda_handler.py` la lee de ahí en cada cold start para armar `POSTGRES_URL`
automáticamente (funciones `_load_secrets_into_env()` y
`_build_postgres_url()`, ver `infra/README.md` punto 7). No tienes que copiar
la contraseña a ningún otro lugar.

### 6.2 Probar el endpoint desplegado

**Desde septiembre 2026 la API requiere un token de Cognito** (ver sección
6.6 para obtenerlo — hazlo antes de seguir, todos los ejemplos de esta guía
a partir de aquí asumen que ya tienes `$COGNITO_ID_TOKEN` exportado):

```bash
export COGNITO_ID_TOKEN="eyJraWQ..."   # ver sección 6.6
python3 -m scripts.smoke_test_deployed_api <ApiUrl> ISIS-1234
```

o directamente con `curl`:

```bash
curl -X POST "<ApiUrl>" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{"studentId":"est-1","courseId":"ISIS-1234","message":"¿Qué es RAG?"}'
```

Sin el header `Authorization`, o con un token expirado (los `IdToken` de
Cognito duran 1 hora por defecto), la respuesta es `401 Unauthorized` — la
rechaza API Gateway antes de que la petición llegue al Lambda, así que no
verás ningún log de `[orchestrator-init]` en CloudWatch para esos intentos.

La primera vez, como el curso `ISIS-1234` no tiene material indexado ni
contexto CAG precargado, la respuesta vendrá sin contexto — eso es esperado y
de todas formas confirma que **todo el camino** (API Gateway → Lambda →
Secrets Manager → Provider Registry → alguno de los 3 proveedores reales)
funciona.

### 6.3 Probar el camino CAG con datos reales

Para ver el camino "usa CAG" funcionando en el despliegue real, precarga un
contexto directamente en la tabla DynamoDB creada por la plantilla:

```bash
aws dynamodb put-item --table-name javeriana-ia-cag-cache --item '{
  "course_id": {"S": "ISIS-1234"},
  "context": {"S": "Reglas del curso: 3 parciales, sin celulares en examen."},
  "version": {"N": "1"}
}'
```

Repite la prueba de 6.2 — ahora la respuesta debería reflejar `used_cag: true`.

### 6.4 Indexar material real para probar RAG (publicar material + consultar)

**Corrección importante:** una versión anterior de esta guía sugería
"invocar el Lambda directamente con un evento que llame a
`ResourceManager.publish_material(...)`" — eso no funciona. El Lambda
desplegado (`lambda_handler.py`) ensambla el Orquestador con
`build_orchestrator()` (`src/config.py`), que **no incluye**
`ResourceManager` ni `CourseCatalogService` — esos módulos solo existen en
`build_application()`, usado por `run_demo.py` y las pruebas, no por el
Lambda real. Su `handler()` solo entiende el contrato de `POST /query`
(`studentId`/`courseId`/`message`); no hay ninguna forma de invocarlo para
que llame a `publish_material`. Tampoco había, al momento de escribir esto,
ninguna ruta HTTP de ingesta.

**Actualización:** esa ruta HTTP de ingesta ya existe —
`POST /courses/{courseId}/material` (FR5, ver sección 6.5) llama
internamente a `ResourceManager.publish_material(...)` y es hoy la forma
recomendada de indexar material real. `scripts/seed_rag_data.py` (descrito
abajo) sigue funcionando como alternativa de más bajo nivel: escribe
directamente en el vector store sin pasar por el catálogo de cursos ni por
`ResourceManager`, útil si necesitas indexar contenido para un `courseId`
que aún no existe en el catálogo, o para depurar el pipeline de ingesta de
forma aislada.

La forma alternativa (de bajo nivel) de probar el escenario "publicar
material y luego preguntar" contra el despliegue en AWS es escribir
directamente en el mismo vector store (`PGVectorStore`, sobre el RDS ya
desplegado) que consulta el Orquestador, usando el mismo pipeline de
ingesta documentado (chunking + embeddings vía Provider Registry). Para
esto se agregó `scripts/seed_rag_data.py`:

```bash
# 1) Apunta POSTGRES_URL al RDS REAL ya desplegado (no localhost) — mismo
#    DBUsername/DBPassword/PostgresEndpoint de la sección 6.1, con pgvector
#    ya habilitado ahí.
export POSTGRES_URL="postgresql+psycopg://<DBUsername>:<DBPassword>@<PostgresEndpoint>:5432/javeriana_ia?connect_timeout=5"

# 2) Gemini es el único de los 3 proveedores con embed() real (ver 3.3) —
#    se necesita para calcular los embeddings del texto que vas a indexar.
export GOOGLE_API_KEY=...

# 3) Indexa un texto real para un curso (usa el MISMO courseId que vas a
#    consultar después vía /query).
python3 -m scripts.seed_rag_data ISIS-1234 \
  "El curso de Estructuras de Datos cubre listas, pilas, colas y árboles. Tiene 3 parciales y un proyecto final."

# 4) Pregunta por ese contenido contra la API ya desplegada (requiere
#    COGNITO_ID_TOKEN exportado — ver sección 6.6).
python3 -m scripts.smoke_test_deployed_api <ApiUrl> ISIS-1234
```

En el resultado del paso 4, confirma dos cosas: `rag_chunks` > 0 (el
Orquestador sí encontró y usó el chunk indexado) y que el texto de la
respuesta refleja el contenido que indexaste (no una respuesta genérica del
LLM sin contexto) — eso es lo que distingue una prueba de "negocio" real de
un simple smoke test que solo confirma que la API responde 200.

Puedes combinar esto con 6.3 (precargar CAG en DynamoDB) para el mismo
`courseId` y así probar en una sola consulta que el Orquestador use ambos
caminos (RAG + CAG) a la vez.

### 6.5 API real de gestión de cursos (FR5): referencia con parámetros y ejemplos

Esta sección responde directamente a la pregunta de si la documentación
detalla las APIs con parámetros y ejemplos: hasta la sección anterior solo
`/query` estaba documentado así; FR3 (artefactos) y FR6 (seguimiento) siguen
sin ruta HTTP (ver "Qué falta" en el README principal). Lo que sigue cubre
las 5 rutas nuevas de FR5, ya desplegadas en el mismo `QueryFunction` (misma
API Gateway, mismo Lambda — decisión D2 de monolito modular).

Usa la salida `ApiBaseUrl` del stack (sección 5.4) como base — es distinta
de `ApiUrl`, que sigue apuntando específicamente a `/query`:

```bash
aws cloudformation describe-stacks --stack-name javeriana-ia-poc \
  --query "Stacks[0].Outputs[?OutputKey=='ApiBaseUrl'].OutputValue" --output text
```

Estas rutas usan `PostgresCourseCatalogService` (persistente, sobre el mismo
RDS que RAG) — a diferencia del catálogo en memoria que usan `run_demo.py` y
los tests, los datos sí sobreviven entre invocaciones del Lambda.

**Las 5 rutas de esta sección requieren el mismo token de Cognito que
`/query`** (sección 6.6) — todos los ejemplos siguientes asumen
`$COGNITO_ID_TOKEN` ya exportado.

**1) `POST /courses` — crear un curso**

| Campo (body JSON) | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `courseId` | string | sí | Identificador único del curso (p.ej. `ISIS-1234`) |
| `name` | string | sí | Nombre visible del curso |

```bash
curl -i -X POST "<ApiBaseUrl>/courses" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{"courseId": "ISIS-1234", "name": "Estructuras de Datos"}'
```

Respuestas: `201` (creado, devuelve el curso) · `409` (`courseId` ya existe,
`DuplicateCourseError`) · `400` (falta `courseId` o `name`) · `502` (no se
pudo construir el catálogo, p.ej. RDS inalcanzable).

**2) `GET /courses` — listar cursos de un usuario**

| Parámetro (query string) | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `userId` | string | sí | Devuelve los cursos donde este usuario es docente o estudiante |

```bash
curl -i "<ApiBaseUrl>/courses?userId=prof-1" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN"
```

Respuesta `200`, cuerpo de ejemplo:

```json
{"courses": [{"courseId": "ISIS-1234", "name": "Estructuras de Datos", "teachers": ["prof-1"], "students": ["est-1", "est-2"]}]}
```

Respuestas: `200` (lista, puede ser vacía) · `400` (falta `userId`) · `502`
(fallo del catálogo).

**3) `POST /courses/{courseId}/teachers` — asignar un docente**

| Parámetro | Ubicación | Obligatorio | Descripción |
|---|---|---|---|
| `courseId` | path | sí | Curso al que se asigna el docente |
| `teacherId` | body JSON | sí | Identificador del docente |

```bash
curl -i -X POST "<ApiBaseUrl>/courses/ISIS-1234/teachers" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{"teacherId": "prof-1"}'
```

Respuestas: `200` (asignado) · `404` (`courseId` no existe,
`CourseNotFoundError`) · `400` (falta `teacherId`) · `502` (fallo del
catálogo).

**4) `POST /courses/{courseId}/students` — inscribir un estudiante**

Mismo contrato que el anterior, con `studentId` en el body en vez de
`teacherId`:

```bash
curl -i -X POST "<ApiBaseUrl>/courses/ISIS-1234/students" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{"studentId": "est-1"}'
```

Respuestas: `200` / `404` / `400` / `502`, igual que en (3).

**5) `POST /courses/{courseId}/material` — publicar material (dispara RAG + CAG)**

Esta es la ruta que reemplaza al workaround de `scripts/seed_rag_data.py`
descrito en 6.4: internamente llama a `ResourceManager.publish_material(...)`,
que indexa el texto en `PGVectorStore` (chunking + embeddings, mismo pipeline
de siempre) y, si se envía `cagContext`, también lo precarga en el CAG Store
(DynamoDB) — en una sola llamada HTTP en vez de dos pasos manuales
separados (6.3 + 6.4).

| Campo (body JSON) | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `documentId` | string | sí | Identificador del documento/material dentro del curso |
| `text` | string | sí | Texto a indexar para RAG |
| `cagContext` | string | no | Si se envía, se precarga como contexto CAG del curso |
| `cagTtlSeconds` | number | no | TTL del contexto CAG anterior, en segundos (si no se envía, usa el default del CAG Store) |

```bash
curl -i -X POST "<ApiBaseUrl>/courses/ISIS-1234/material" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{
    "documentId": "syllabus-2026-2",
    "text": "El curso de Estructuras de Datos cubre listas, pilas, colas y árboles. Tiene 3 parciales y un proyecto final.",
    "cagContext": "Reglas del curso: 3 parciales, sin celulares en examen.",
    "cagTtlSeconds": 604800
  }'
```

Respuestas: `200` (publicado) · `404` (`courseId` no existe) · `400` (falta
`documentId` o `text`) · `502` (fallo de catálogo, vector store o CAG
store).

Tras publicar, repite la prueba de 6.2 (`smoke_test_deployed_api.py` o
`curl` contra `/query`) con el mismo `courseId` — deberías ver `rag_chunks`
> 0 y, si enviaste `cagContext`, `used_cag: true`, ambos en una sola llamada
de publicación en vez de los pasos manuales de 6.3/6.4.

Las 12 pruebas de estas 5 rutas están en `tests/test_lambda_handler_helpers.py`
(clase `TestCourseRoutes`), con el catálogo en memoria inyectado — no
requieren PostgreSQL real para correr.

### 6.6 Obtener un token de Cognito (para Postman, curl o cualquier cliente)

**Desde septiembre 2026 las 6 rutas de la API (`/query` y las 5 de
`/courses...`) requieren un JWT válido de Cognito** — antes cualquiera con
la URL podía invocar la API sin ningún token; ahora API Gateway rechaza con
`401` cualquier petición sin un `Authorization: Bearer <token>` válido,
antes de que llegue al Lambda. Esto se implementó agregando un autorizador
de Cognito (`JaverianaApi`, `infra/template.yaml`) sobre el `UserPool` que
ya existía como recurso pero no estaba conectado a ninguna ruta.

**Qué SÍ resuelve y qué NO.** Esto valida *quién* llama (un usuario real,
autenticado, con un token no expirado) — cierra la brecha de la API abierta
a cualquiera en internet. **No** valida *qué* puede hacer ese usuario (p.ej.
que un estudiante no pueda publicar material o consultar el avance de otro
curso al que no pertenece): `lambda_handler.py` sigue confiando en el
`studentId`/`courseId` que manda el cliente en el body de la petición, sin
cruzarlo contra el token. Ese control más fino (el "Authorization Policy
Engine" con RBAC sobre `courseId`) sigue siendo diseño de la Iteración 7 del
documento de arquitectura, no código — ver la nota en `template.yaml` junto
al recurso `JaverianaApi`.

**Paso 1 — crear un usuario de prueba en el User Pool** (una sola vez):

```bash
USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name javeriana-ia-poc \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)
CLIENT_ID=$(aws cloudformation describe-stacks --stack-name javeriana-ia-poc \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" --output text)

aws cognito-idp sign-up --client-id "$CLIENT_ID" \
  --username "estudiante-prueba@example.com" \
  --password "Prueba123456" \
  --user-attributes Name=email,Value="estudiante-prueba@example.com"

# Confirma el usuario sin pasar por el correo de verificación (solo para
# pruebas — en producción el usuario se confirma solo, vía el flujo de SSO
# institucional que la Iteración 7 describe y que este PoC no implementa).
aws cognito-idp admin-confirm-sign-up --user-pool-id "$USER_POOL_ID" \
  --username "estudiante-prueba@example.com"
```

**Paso 2 — autenticarse y obtener el token:**

```bash
RESPUESTA=$(aws cognito-idp initiate-auth --client-id "$CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME="estudiante-prueba@example.com",PASSWORD="Prueba123456")

export COGNITO_ID_TOKEN=$(echo "$RESPUESTA" | python3 -c "import json,sys; print(json.load(sys.stdin)['AuthenticationResult']['IdToken'])")
```

El `IdToken` (no el `AccessToken`) es el que espera el autorizador de
Cognito de API Gateway para REST APIs — dura **1 hora** por defecto; pasado
ese tiempo, repite este paso (o usa el `RefreshToken` de la misma respuesta
con `--auth-flow REFRESH_TOKEN_AUTH` para renovarlo sin volver a pedir
usuario/contraseña).

**Paso 3 — usarlo con `curl`** (ya reflejado en los ejemplos de 6.2/6.5):

```bash
curl -i -X POST "<ApiUrl>" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $COGNITO_ID_TOKEN" \
  -d '{"studentId":"est-1","courseId":"ISIS-1234","message":"¿Qué es RAG?"}'
```

**Paso 4 — usarlo en Postman:**

1. Ejecuta los pasos 1-2 en tu terminal y copia el valor de `IdToken`
   (imprímelo con `echo $COGNITO_ID_TOKEN`).
2. En la petición de Postman, pestaña **Authorization** → tipo **Bearer
   Token** → pega el token ahí (Postman agrega el header
   `Authorization: Bearer <token>` automáticamente); o, si prefieres el
   header a mano, en la pestaña **Headers** agrega
   `Authorization` = `Bearer <token pegado, sin comillas>`.
3. Recuerda repetir el paso 1-2 cuando el token expire (1 hora) — Postman no
   lo renueva solo; si quieres automatizarlo, un *Pre-request Script* de la
   colección puede llamar `initiate-auth` vía una petición HTTP a Cognito y
   guardar el `IdToken` en una variable de colección, pero eso ya es
   configuración de Postman, no de este proyecto.

---

## 7. Monitoreo

- **Logs:** `aws logs tail /aws/lambda/javeriana-ia-poc-query --follow`
- **Alarmas creadas por la plantilla:** `QueryFunctionErrorsAlarm` (errores) y
  `QueryFunctionLatencyAlarm` (p95 > 8 s, el umbral de degradación de QAS-P3) —
  visibles en CloudWatch → Alarms.
- **Costo:** revisa Billing → Cost Explorer semanalmente mientras el PoC esté
  desplegado; la instancia RDS es el componente que corre 24/7 y por lo tanto
  el que más "quema" las 750 horas/mes del free tier legacy o los créditos del
  Free Plan nuevo.

---

## 8. Resumen: cómo correr cada tipo de prueba

| Qué quieres probar | Comando | Requiere |
|---|---|---|
| Lógica de la arquitectura (circuit breaker, fallback, RAG/CAG, cursos, artefactos) | `python3 -m unittest discover -s tests -v` | Nada (Python estándar) |
| Demo narrado de los 12 escenarios | `python3 run_demo.py` | Nada |
| Los 3 proveedores LLM reales + (opcional) Postgres real, en tu máquina | `python3 -m scripts.smoke_test_real_providers` | `.env` con las 3 API keys (sección 2–3) |
| API ya desplegada en AWS | `python3 -m scripts.smoke_test_deployed_api <ApiUrl>` | Stack desplegado (sección 5) + `COGNITO_ID_TOKEN` exportado (sección 6.6) |
| Camino CAG en AWS con datos reales | `aws dynamodb put-item ...` + prueba anterior | Stack desplegado |

---

## 9. Apagar todo (evitar costos después de terminar)

```bash
sam delete --stack-name javeriana-ia-poc
```

Esto borra Lambda, API Gateway, DynamoDB, Cognito, Secrets Manager y el
bucket S3. **Revisa manualmente que la instancia RDS también se haya
eliminado** (la plantilla tiene `DeletionPolicy: Snapshot`, así que además
puede dejar un snapshot final — bórralo si no lo necesitas):

```bash
aws rds describe-db-instances --query "DBInstances[].DBInstanceIdentifier"
aws rds describe-db-snapshots --query "DBSnapshots[].DBSnapshotIdentifier"
```

---

## 10. Problemas comunes

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `sam build` falla por tamaño del paquete | LangChain + dependencias exceden el límite de Lambda | Usar `--use-container` o mover dependencias a una Lambda Layer |
| `PythonPipBuilder:Validation - Binary validation failed for python ... which did not satisfy constraints for runtime: pythonX.Y` | `sam build` (sin `--use-container`) busca en tu PATH exactamente la versión de Python del `Runtime` de la plantilla, y tu máquina tiene otra (p.ej. tienes 3.13 pero la plantilla pedía 3.11) | Usar `sam build -t infra/template.yaml --use-container` (recomendado siempre en este proyecto, ver 5.2) — no depende de qué Python tengas localmente |
| El Lambda desplegado falla con `Runtime.ImportModuleError` al llamar a Postgres | Se construyó sin `--use-container`: `psycopg[binary]` quedó compilado para tu sistema operativo local (macOS/Windows), no para Amazon Linux | Repetir `sam build -t infra/template.yaml --use-container` y volver a `sam deploy` |
| `sam deploy` falla en `PostgresDB` con `"Cannot find version X.Y for postgres"` | RDS retira minor versions específicas de PostgreSQL con frecuencia (p.ej. `16.4` ya no estaba disponible en agosto de 2026); la plantilla tenía esa minor fijada | Ya corregido: la plantilla usa solo la versión mayor (`EngineVersion: "16"`), y RDS asigna automáticamente la minor vigente — no requiere acción si usas la versión más reciente de este repo. Si vuelve a pasar con otra versión mayor, revisa las minors disponibles con `aws rds describe-db-engine-versions --engine postgres --query "DBEngineVersions[].EngineVersion"` |
| `sam deploy` vuelve a fallar de inmediato tras un intento anterior fallido, sin ni siquiera empezar a crear recursos | Cuando `CREATE_FAILED` dispara un rollback (como en las dos filas siguientes), el stack queda en estado `ROLLBACK_COMPLETE` — CloudFormation no permite desplegar de nuevo sobre un stack en ese estado | Ejecutar `sam delete --stack-name <nombre-del-stack>` primero (no genera costo extra: los recursos que fallaron a medio crear ya se borraron en el rollback), y recién después repetir `sam build --use-container` + `sam deploy --guided` |
| `sam deploy` falla en `QueryFunctionLatencyAlarm` con `"Value 'p95' at 'statistic' failed to satisfy constraint"` | `AWS::CloudWatch::Alarm` solo acepta `Maximum/SampleCount/Sum/Minimum/Average` en la propiedad `Statistic`; los percentiles (`p95`, `p99`, etc.) van en `ExtendedStatistic`, una propiedad distinta | Ya corregido: la plantilla usa `ExtendedStatistic: p95` en vez de `Statistic: p95`. Si ves este error es que estás en una versión anterior del repo — actualízala, borra el stack (`sam delete`) y reintenta |
| `psql` no conecta a RDS | Tu IP no está en `DBAllowedCidr`, o la instancia sigue creándose | Verificar con `aws rds describe-db-instances --query "DBInstances[0].DBInstanceStatus"` |
| La respuesta del `/query` siempre dice "sin contexto disponible" | No hay material indexado (RAG) ni contexto precargado (CAG) para ese `courseId` | Ver sección 6.3/6.4 |
| Error de autenticación con alguno de los 3 proveedores en Lambda | El secreto en Secrets Manager no coincide con las claves reales, o el Lambda no tiene permiso `secretsmanager:GetSecretValue` | Revisar `LLMSecretsArn` en las salidas del stack y la policy del `QueryFunction` en `infra/template.yaml` |
| El modelo configurado (Gemini, Groq u OpenRouter) responde error 404/"model not found" | Deprecación de modelo (ver sección 0.1); los proveedores gratuitos cambian sus modelos con frecuencia | Revisar `console.groq.com/docs/deprecations`, `ai.google.dev/gemini-api/docs/changelog` y `openrouter.ai/models`; actualizar `GEMINI_MODEL`/`OPENAI_OSS_MODEL`/`LLAMA_MODEL` |
| El modelo `:free` de OpenRouter responde 429 (rate limit) | Los modelos `:free` tienen límites de tasa más estrictos que los de pago | Reintentar tras unos segundos, o bajar la prioridad de Llama 3.3 en `src/config.py` si se usa mucho en pruebas |
| `AllProvidersFailedError` al llamar `registry.embed(...)` | Groq y OpenRouter no soportan `/embeddings` (`NotImplementedError`, esperado y no cuenta como falla); si Gemini también aparece en el detalle del error, es una falla real de Gemini — el mensaje ya lista el error de cada proveedor por separado, no solo el último | Lee el detalle completo del mensaje; si el error de Gemini menciona un modelo no encontrado, revisa `GEMINI_EMBEDDING_MODEL` (ver 0.1: `text-embedding-004` se apagó el 14/01/2026) |
| La cuenta de AWS se cerró sola | Se agotaron los USD 200 de crédito o pasaron 6 meses (cuentas nuevas, ver sección 0) | Pasar a un plan pago dentro de los 90 días de gracia para recuperar los datos |
| `scripts/smoke_test_deployed_api.py` falla con `requests.exceptions.ReadTimeout` (`read timeout=15`) | El cliente usaba un timeout fijo de 15s, demasiado corto para la primera invocación (cold start): Lambda importa LangChain/boto3/psycopg, abre conexión a RDS y Secrets Manager, y solo después llama a los proveedores LLM | Ya corregido: el script ahora usa timeout=45s por defecto (configurable como 3.er argumento). Si con 45s sigue fallando, revisa CloudWatch Logs (ver la fila siguiente) |
| CloudWatch Logs de `QueryFunction` muestran `REPORT ... Duration: 28000.00 ms ... Status: timeout`, y justo antes un warning de "Direct use of automatic function calling (AFC)..." | El `timeout=8s` que se le pasaba a `ChatGoogleGenerativeAI` (adaptador de Gemini) no se respetó — la llamada se quedó colgada hasta que el propio `Timeout` de la función Lambda (28s) mató la ejecución, sin que el circuit breaker llegara a conmutar a Groq/OpenRouter. Coincide con un issue conocido de `langchain-google-genai` (ver Fuentes) | Ya corregido: el tope de tiempo ahora lo aplica `ProviderRegistry` con un timeout de PARED por hilo (`LLM_REQUEST_TIMEOUT_SECONDS=6`, ver sección 0.2), que deja de esperar a un proveedor colgado sin depender de que su SDK respete ningún parámetro propio. Si ves este mismo síntoma en una versión anterior del repo, actualízala y vuelve a `sam build --use-container` + `sam deploy` (no hace falta `sam delete`, ver más abajo) |
| `health_check`/`generate()` de Gemini falla con `400 INVALID_ARGUMENT: "Manually set deadline Ns is too short. Minimum allowed deadline is 10s."` | `LLM_REQUEST_TIMEOUT_SECONDS` está por debajo de 10s y se estaba pasando directo al SDK de Google (`ChatGoogleGenerativeAI`/`GoogleGenerativeAIEmbeddings`), que rechaza cualquier deadline menor a 10s — es un piso duro de la API de Google, no una recomendación | Ya corregido: `gemini_adapter.py` aplica `max(LLM_REQUEST_TIMEOUT_SECONDS, 10.0)` solo para el parámetro que le pasa al SDK de Gemini; el timeout de PARED de `ProviderRegistry` sigue usando el valor sin recortar (6s) para decidir cuándo dejar de esperarlo, así que el presupuesto de tiempo del Lambda no se ve afectado |
| CloudWatch Logs de `QueryFunction` muestran `Status: timeout` a los 28000ms exactos, pero SIN ningún log intermedio (ni el warning de Gemini), tanto en invocaciones frías como calientes | El colgado ocurre antes de llamar a cualquier proveedor LLM — durante la construcción de `PGVectorStore` (conexión a RDS sin `connect_timeout`), que se reintenta en cada invocación porque nunca llega a cachearse en `_orchestrator` | Ya corregido: `_build_postgres_url()` agrega `connect_timeout=5` (configurable con `POSTGRES_CONNECT_TIMEOUT_SECONDS`) a la URL de Postgres, y los clientes de Secrets Manager/DynamoDB fijan timeouts explícitos vía `botocore.config.Config`. Si sigue colgado incluso con esto, revisa la fila siguiente (checkpoints de diagnóstico) para ver exactamente dónde |
| El mismo `Status: timeout` a los 28000ms exactos persiste incluso después de aplicar los `connect_timeout` de la fila anterior, y sigues sin ver ningún log intermedio | Sin instrumentación no hay forma de saber en qué línea se quedó — Lambda no deja traceback cuando mata una invocación por Timeout (a diferencia de una excepción real) | Ya corregido: `lambda_handler.py` ahora imprime checkpoints (`[orchestrator-init] ...`) con el tiempo transcurrido en cada etapa. Vuelve a desplegar y revisa CloudWatch: la última línea impresa antes del corte te dice exactamente dónde seguir investigando (p.ej. si el último checkpoint es "construyendo PGVectorStore..." sin el siguiente, es conectividad real a RDS — revisa `DBAllowedCidr`, que la instancia esté `available` con `aws rds describe-db-instances`, y que se haya ejecutado `CREATE EXTENSION vector;`) |
| Los checkpoints muestran `build_provider_registry() completado (+18-20s)` — el cuello de botella está ANTES de tocar RDS o cualquier proveedor LLM | Los imports de `langchain-google-genai`/`langchain-openai` (grpc, protobuf, openai, httpx) son pesados y `MemorySize: 512` solo asigna ~0.29 vCPU (la CPU escala lineal con la memoria hasta 1792 MB = 1 vCPU) | Ya corregido y confirmado: con `MemorySize: 1024` el mismo paso bajó a ~9s (la mitad) y la invocación completa terminó en ~15.6s, muy por debajo de los 28s de Timeout, sin `Status: timeout`. Si en el futuro vuelve a acercarse al límite, subir a ~1792 MB (1 vCPU completo) o mover las dependencias pesadas a una Lambda Layer |
| Cualquier ruta responde `401 Unauthorized` sin llegar a generar ningún log `[orchestrator-init]` en CloudWatch | Falta el header `Authorization: Bearer <token>`, o el `IdToken` de Cognito expiró (dura 1 hora por defecto) — el rechazo lo hace el autorizador de API Gateway (`JaverianaApi`), antes de invocar el Lambda | Repite el paso 2 de la sección 6.6 (`aws cognito-idp initiate-auth`) para obtener un token fresco, o usa el `RefreshToken` de la misma respuesta con `--auth-flow REFRESH_TOKEN_AUTH` |
| `aws cognito-idp initiate-auth` falla con `NotAuthorizedException: Password attempts exceeded` o `UserNotConfirmedException` | El usuario de prueba no se confirmó (`admin-confirm-sign-up`, paso 1 de la sección 6.6), o se superó el límite de intentos fallidos de Cognito | Confirma el usuario con `admin-confirm-sign-up` si no lo habías hecho; si es por intentos fallidos, espera unos minutos o crea un usuario de prueba nuevo |

---

## Checklist final

- [ ] Pruebas locales (91/91) y demo corren sin errores
- [ ] `smoke_test_real_providers.py` responde con los 3 proveedores (Gemini, Groq/gpt-oss-20b, OpenRouter/Llama 3.3)
- [ ] Créditos cargados en la cuenta de OpenRouter, unos pocos dólares (Settings → Credits) — Llama 3.3 es de pago por defecto (ver 0.1)
- [ ] Alarma de presupuesto creada en AWS Budgets (`infra/budget/`, ver 4.2) — techo acordado de USD 20 combinado AWS + IA
- [ ] `sam validate` y `sam build --use-container` (con Docker corriendo) sin errores
- [ ] `sam deploy --guided` completado, salidas del stack guardadas
- [ ] `CREATE EXTENSION vector;` ejecutado en RDS
- [ ] Usuario de prueba creado y confirmado en el User Pool, token de Cognito obtenido (sección 6.6)
- [ ] `smoke_test_deployed_api.py` (con `COGNITO_ID_TOKEN` exportado) responde HTTP 200
- [ ] Prueba del camino CAG (sección 6.3) responde `used_cag: true`
- [ ] Alarmas de CloudWatch visibles y en estado OK
- [ ] Sabes cómo y cuándo vas a correr `sam delete` para no seguir generando costo

---

## Fuentes

- [AWS Free Tier now offers $200 in credits and 6-month free plan to explore AWS at no cost](https://aws.amazon.com/about-aws/whats-new/2025/07/aws-free-tier-credits-month-free-plan/)
- [AWS Free Tier 2025 Restructuring: $200 Credits vs 12-Month Trial — Tech Bytes](https://www.techbytes.app/posts/aws-free-tier-2025-restructuring-developer-guide/)
- [AWS Free Tier in 2026: What Changed, What's Still Free — InfraTally](https://infratally.com/articles/aws-free-tier-2026.html)
- [AWS RDS Free Tier | What 750 Hours Actually Gets You — Usage.ai](https://www.usage.ai/blogs/aws/reserved-instances/rds/free-tier-rds/)
- [db.t3.micro pricing — Economize Cloud](https://www.economize.cloud/resources/aws/pricing/rds/db.t3.micro/) (~USD 21.90/mes sin capa gratuita, actualizado jul 2026)
- [AWS RDS Cost (2026): A Complete Breakdown of Every Charge — Selfhost.dev](https://selfhost.dev/blog/aws-rds-cost-breakdown-2026/)
- [AWS::Budgets::Budget — AWS CloudFormation Template Reference](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-budgets-budget.html)
- [Amazon RDS for PostgreSQL supports minor versions 18.4, 17.10, 16.14, 15.18, and 14.23 — AWS](https://aws.amazon.com/about-aws/whats-new/2026/05/amazon-rds-postgresql/) (confirma que `16.4` ya fue retirada)
- [Available PostgreSQL database versions — Amazon RDS User Guide](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.DBVersions.html)
- [AWS::CloudWatch::Alarm — AWS CloudFormation Template Reference](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-cw-alarm.html) (`Statistic` vs. `ExtendedStatistic`)
- [AWS Lambda Cost Breakdown For 2026 — Wiz](https://www.wiz.io/academy/cloud-cost/aws-lambda-cost-breakdown)
- [AWS API Gateway Pricing Calculator & Guide (Aug 2026) — Costgoat](https://costgoat.com/pricing/amazon-api-gateway)
- [Using Gemini API keys | Google AI for Developers](https://ai.google.dev/gemini-api/docs/api-key)
- [How to Get a Gemini API Key Free (Google AI Studio) — TinkerLLM](https://tinkerllm.com/blog/how-to-get-gemini-api-key-free/)
- [Gemini API release notes | Google AI for Developers](https://ai.google.dev/gemini-api/docs/changelog)
- [Quotas for configuring and running a REST API in API Gateway — AWS](https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-execution-service-limits-table.html) (tope de integración de 29s por defecto; se puede pedir aumento de cuota, no aplicado en este proyecto)
- [Timeout configuration not respected by `ChatGoogleGenerativeAI` — langchain-ai/langchain-google Issue #731](https://github.com/langchain-ai/langchain-google/issues/731)
- [Understanding the Lambda execution environment lifecycle — AWS](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html) (fase Init limitada a 10s sin provisioned concurrency/SnapStart; si no alcanza, se reintenta en la primera invocación con el Timeout de la función)
- [How to Choose the Right Lambda Memory Size for Performance — OneUptime](https://oneuptime.com/blog/post/2026-02-12-choose-right-lambda-memory-size-performance/view) (la CPU escala linealmente con la memoria hasta 1792 MB = 1 vCPU completo)
- [Groq API Free Tier Limits in 2026 — Grizzly Peak Software](https://www.grizzlypeaksoftware.com/articles/p/groq-api-free-tier-limits-in-2026-what-you-actually-get-uwysd6mb)
- [Groq Free Tier Limits 2026 — TokenMix](https://tokenmix.ai/blog/groq-free-tier-limits-2026)
- [Model Deprecation — GroqDocs (fecha de apagado de `llama-3.1-8b-instant`: 16/08/2026)](https://console.groq.com/docs/deprecations)
- [Gemini 2.5 and 2.0 Model Shutdowns and Replacements 2026 — PocketLantern](https://pocketlantern.dev/briefs/gemini-2-0-and-2-5-model-shutdowns-and-replacements-2026)
- [fix: update default Gemini model from gemini-2.5-flash to gemini-3.5-flash (shutdown Oct 16 2026) — GitHub](https://github.com/NousResearch/hermes-agent/issues/32360)
- [Free LLM API in 2026: 13 Options Ranked and Compared — OpenRouter Blog](https://openrouter.ai/blog/tutorials/free-llm-apis-compared/)
- [Meta: Llama 3.3 70B Instruct (free) — OpenRouter](https://openrouter.ai/meta-llama/llama-3.3-70b-instruct:free) (catálogo muestra "Free", 131K contexto; en pruebas reales el slug `:free` respondió 404 "unavailable for free" — ver 0.1/2.3)
- [Why do all free models return a 404 (data policy) — OpenRouter Help Center](https://openrouter.zendesk.com/hc/en-us/articles/51690904755227-Why-do-all-free-models-return-a-404-No-endpoints-available-matching-your-guardrail-restrictions-and-data-policy)
- [Gemini Embedding now generally available in the Gemini API — Google Developers Blog](https://developers.googleblog.com/gemini-embedding-available-gemini-api/) (`text-embedding-004` apagado 14/01/2026; reemplazo `gemini-embedding-001`)
- [Embeddings | Gemini API | Google AI for Developers](https://ai.google.dev/gemini-api/docs/embeddings)
- [AWS Lambda adds support for Python 3.13](https://aws.amazon.com/about-aws/whats-new/2024/11/aws-lambda-support-python-313/) (LTS hasta oct 2029)
- [Building Lambda functions with Python — Build with `--use-container` — AWS SAM Developer Guide](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-using-build.html)
- [Together AI Serverless Models (Llama 3.3 70B, de pago)](https://docs.together.ai/docs/serverless-models)
- [Cerebras Inference — Models](https://inference-docs.cerebras.ai/models) (confirmado: sin modelos Llama en tier gratuito, agosto 2026)
