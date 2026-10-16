# Despliegue en AWS (capa gratuita) — plantilla SAM

Esta plantilla (`template.yaml`) sigue la vista de despliegue documentada en las
Iteraciones 1 y 3 del diseño ADD 3.0 (`arquitectura-add30-javeriana.md`).

**Importante:** esta plantilla se validó solo sintácticamente (YAML bien formado,
referencias `Ref`/`GetAtt`/`Sub` consistentes) en el entorno donde se construyó la
PoC, que no tiene credenciales de AWS ni acceso a internet. No se ha desplegado ni
probado contra una cuenta real.

**Techo de costo del proyecto:** el equipo fijó un límite combinado de
**menos de USD 20** (AWS + ejecución de modelos de IA). Antes de desplegar,
crea la alarma de presupuesto en `infra/budget/` (ver su README) — el
detalle completo de por qué el riesgo real de costo está casi todo en AWS
(pospago) y no en los proveedores LLM (gratuitos o prepagos) está en
`GUIA_DESPLIEGUE_Y_PRUEBAS.md`, sección 4.2.

Antes de aplicar la plantilla principal:

```bash
sam validate --template infra/template.yaml
sam build --use-container   # instala requirements.txt dentro de un contenedor Lambda real
sam deploy --guided
```

**Usa siempre `--use-container`** (requiere Docker corriendo localmente):
sin él, `sam build` instala las dependencias con tu Python e intérprete
locales (macOS/Windows), lo que produce binarios incompatibles con Amazon
Linux (el SO real de Lambda) para paquetes con partes compiladas como
`psycopg[binary]` — el Lambda desplegado fallaría en tiempo de ejecución.
También evita el error `PythonPipBuilder:Validation - Binary validation
failed for python ...` que aparece si tu máquina no tiene instalada
exactamente la versión de Python del `Runtime` de la plantilla. Ver
`GUIA_DESPLIEGUE_Y_PRUEBAS.md`, sección 5.2.

## Qué crea

| Recurso | Servicio AWS | Corresponde a |
|---|---|---|
| `FrontendBucket` | S3 (hosting estático) | Cliente Web |
| `UserPool` / `UserPoolClient` | Cognito | Autenticación (QAS-S1) |
| `JaverianaApi` | API Gateway (REST, explícita) con autorizador de Cognito (`DefaultAuthorizer`) | Las 6 rutas requieren `Authorization: Bearer <JWT>` — ver GUIA sección 6.6 |
| `CAGTable` | DynamoDB (TTL nativo en `expires_at`) | Caché KV / CAG Store |
| `LLMSecrets` | Secrets Manager | Credenciales de generate() (Gemini/OpenAI/Llama) y del respaldo 2 de embed() (Voyage AI) (QAS-S2) |
| `PostgresDB` | RDS PostgreSQL, single-AZ | Persistencia relacional + pgvector |
| `QueryFunction` | Lambda, expuesto vía `JaverianaApi` (`POST /query`, y 5 rutas `/courses/...` para FR5 — ver GUIA sección 6.5) | Orquestador + Adaptadores + gestión de cursos/recursos |
| `QueryFunctionErrorsAlarm` / `...LatencyAlarm` | CloudWatch | Observabilidad (QAS-D1/P1/P3) |

## Parámetros que debes proveer

- `VpcId` y `SubnetIds` (≥2 subredes en distintas AZ): puedes usar la VPC default.
  Consulta con: `aws ec2 describe-subnets --filters Name=default-for-az,Values=true`
- `DBUsername` / `DBPassword`: credenciales de PostgreSQL.
- `GoogleApiKey`: clave gratuita de [Google AI Studio](https://aistudio.google.com/) —
  proveedor 1 (primario).
- `OpenAiOssApiKey` / `OpenAiOssBaseUrl`: credenciales del endpoint compatible OpenAI
  que hospeda `openai/gpt-oss-20b` (por defecto Groq, tier gratuito) — proveedor 2.
- `LlamaApiKey` / `LlamaBaseUrl`: credenciales del endpoint compatible OpenAI que
  hospeda Meta Llama 3.3 (por defecto OpenRouter) — proveedor 3. Usa el
  modelo de pago (`meta-llama/llama-3.3-70b-instruct`, no `:free`): ese slug
  dejó de estar disponible en la práctica en OpenRouter — responde 404 "This
  model is unavailable for free", aunque el catálogo web lo siga mostrando
  como "Free" (el tier gratuito de OpenRouter cambia de disponibilidad con
  frecuencia). La cuenta de OpenRouter necesita créditos cargados (ver
  GUIA_DESPLIEGUE_Y_PRUEBAS.md, sección 0.1).
- **`BedrockEmbeddingModelId`** (respaldo 1 de `embed()`, agregado septiembre
  2026 — ver R-7 en `EVALUACION_ATAM_LITE.md`): por defecto
  `amazon.titan-embed-text-v2:0`. **No requiere parámetro de credencial**: la
  plantilla concede el permiso `bedrock:InvokeModel` al rol de ejecución de
  `QueryFunction` directamente (ver "Qué crea" arriba). Antes de desplegar,
  verifica en la consola de AWS (Bedrock → Model access) que el modelo Titan
  Text Embeddings V2 esté habilitado en tu cuenta/región — es un paso manual
  de la cuenta, igual que habilitar `pgvector` en RDS (punto 3 abajo).
- `VoyageApiKey` / `VoyageModel` / `VoyageBaseUrl`: respaldo 2 de `embed()`.
  Clave gratuita (200M tokens) en https://dash.voyageai.com/ — proveedor
  independiente de AWS, para no depender de un único cloud si Bedrock
  también tuviera una degradación de servicio.

## ¿Hay que crear la base de datos o el usuario de PostgreSQL a mano?

**No.** `AWS::RDS::DBInstance` (`PostgresDB` en la plantilla) recibe `DBName:
javeriana_ia` (fijo) y `MasterUsername`/`MasterUserPassword` (tus parámetros
`DBUsername`/`DBPassword`). RDS crea automáticamente esa base y ese usuario
maestro al aprovisionar la instancia — no hay ningún paso manual de creación.

Lo único manual que sí hay que hacer, porque CloudFormation no puede ejecutar
SQL dentro de la base, es habilitar la extensión `pgvector` una vez que la
instancia está lista (ver punto 3 abajo y GUIA_DESPLIEGUE_Y_PRUEBAS.md,
sección 6.1):

```bash
psql "postgresql://<DBUsername>:<DBPassword>@<PostgresEndpoint>:5432/javeriana_ia" \
     -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Para conectar el Lambda a esa base, la contraseña **no** se pasa como
variable de entorno en texto plano: se guarda en `LLMSecrets` (Secrets
Manager) junto con las 3 API keys de LLM, y `lambda_handler.py` la lee ahí en
cada cold start para armar `POSTGRES_URL` (ver punto 7 abajo). Tú no tienes
que hacer nada adicional para esto — ya queda conectado con solo llenar los
parámetros al desplegar.

## Limitaciones y trade-offs declarados (ver también el documento de arquitectura)

1. **Sin Multi-AZ en RDS** (`MultiAZ: false`): la capa gratuita de AWS no incluye
   Multi-AZ. Esto viola parcialmente QAS-D2 en el PoC; ya está documentado como
   limitación conocida en la Iteración 3.
2. **RDS públicamente accesible** (`PubliclyAccessible: true`, `DBAllowedCidr`
   configurable): se optó por esto para que el Lambda **no** tenga que unirse a la
   VPC — si lo hiciera, necesitaría un NAT Gateway (no gratuito) para seguir
   teniendo salida a internet y poder llamar a las APIs de Gemini/Llama. Esta es
   una concesión de seguridad aceptable **solo para un PoC académico temporal**;
   en producción, usar una VPC privada con NAT Gateway o VPC endpoints, y restringir
   `DBAllowedCidr` a los security groups específicos.
3. **pgvector no se habilita declarativamente**: después de crear la instancia,
   conéctate y ejecuta `CREATE EXTENSION IF NOT EXISTS vector;` manualmente.
4. **Empaquetado de LangChain en Lambda**: `sam build --use-container` instala
   `requirements.txt` automáticamente dentro del contenedor, pero el paquete
   final puede acercarse al límite de tamaño de Lambda (250 MB descomprimido).
   Si lo excede, considera separar dependencias pesadas en una Lambda Layer o
   usar una imagen de contenedor para la función.
5. **Federación SSO institucional**: el `UserPool` de Cognito se creó sin federar
   con el IdP real de la Universidad — para producción hay que agregar un
   `AWS::Cognito::UserPoolIdentityProvider` con los metadatos SAML/OIDC del IdP
   institucional (no disponibles en este ejercicio). Desde septiembre de 2026
   el `UserPool` sí quedó conectado como autorizador de las 6 rutas de la API
   (`JaverianaApi`, ver "Qué crea" arriba) — esta limitación es solo sobre la
   federación con el IdP, no sobre si Cognito protege o no la API.
6. **Sin autorización granular (RBAC) por curso**: el autorizador de Cognito
   valida que el JWT sea de un usuario real y no haya expirado, pero
   `lambda_handler.py` sigue confiando en el `studentId`/`courseId`/`teacherId`
   que manda el cliente en el body de cada petición — no los cruza contra el
   token ni contra la matrícula real. Un usuario autenticado con cualquier
   token válido puede hoy publicar material o inscribir estudiantes en un
   curso al que no pertenece. El "Authorization Policy Engine" con RBAC sobre
   `courseId` que resolvería esto (Iteración 7 de `arquitectura-add30-javeriana.md`)
   sigue siendo diseño, no código.
7. **`lambda_handler.py` ya usa los recursos reales**: detecta las variables de
   entorno `POSTGRES_HOST` y `DYNAMO_TABLE_NAME` (las que esta plantilla
   inyecta en el Lambda) y en ese caso usa `PGVectorStore` y `DynamoCAGStore`
   en vez de las implementaciones en memoria — así el `CAGTable` de DynamoDB
   que se crea aquí sí queda conectado al Orquestador.
8. **Credenciales resueltas desde Secrets Manager en cada cold start**: la
   plantilla solo inyecta como variables de entorno del Lambda el host/puerto/
   db/usuario de PostgreSQL y los modelos/URLs de los proveedores LLM —
   nunca contraseñas ni API keys en texto plano. `lambda_handler.py` lee el
   secreto `LLMSecrets` (vía `SECRETS_ARN`) con `_load_secrets_into_env()` y
   ensambla `POSTGRES_URL` con `_build_postgres_url()` antes de construir el
   Orquestador. Esto se agregó porque, sin este paso, el secreto se creaba
   pero nada lo leía: el Lambda desplegado se habría quedado sin API keys y
   habría intentado conectarse a `localhost` en vez de a la instancia RDS
   real. Probado en `tests/test_lambda_handler_helpers.py`.
9. **Habilitar el acceso al modelo de Bedrock es un paso manual de la cuenta,
   no de la plantilla**: `AWS::IAM` puede conceder `bedrock:InvokeModel` de
   forma declarativa (ya lo hace esta plantilla), pero AWS exige además
   habilitar explícitamente el acceso a cada modelo base en la consola
   (Bedrock → Model access) al menos una vez por cuenta/región — sin ese
   paso, `BedrockEmbeddingAdapter` recibiría `AccessDeniedException` aunque
   el permiso IAM esté bien configurado. Agregado septiembre 2026 junto con
   el respaldo de `embed()` (ver R-7 en `EVALUACION_ATAM_LITE.md`).

Ver `GUIA_DESPLIEGUE_Y_PRUEBAS.md` (raíz del proyecto) para el procedimiento
completo paso a paso, incluidos los cambios de julio de 2025 en la capa
gratuita de AWS y cómo probar el despliegue.
