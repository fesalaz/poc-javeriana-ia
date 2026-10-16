"""
Prueba de humo contra la API ya desplegada en AWS (POST /query). No se ejecuta
en el entorno de verificación de este proyecto (requiere el stack desplegado)
— úsalo después de `sam deploy` (ver guía de despliegue, sección 6).

Ejecutar:  python3 -m scripts.smoke_test_deployed_api <ApiUrl> [courseId] [timeout_seconds]
Ejemplo:   python3 -m scripts.smoke_test_deployed_api \
             https://abc123.execute-api.us-east-1.amazonaws.com/Prod/query ISIS-1234

NOTA (agosto 2026): el timeout del cliente HTTP por defecto es 45s, no 15s.
La primera invocación de la función Lambda (cold start) instala e importa
LangChain/boto3/psycopg y abre conexión con RDS y Secrets Manager antes de
llamar a los proveedores LLM — es normal que tarde más de 15s la primera vez.
Si de todos modos ves un ReadTimeout con 45s, no es un problema del cliente:
revisa CloudWatch Logs del grupo /aws/lambda/javeriana-ia-poc-query (busca la
línea "REPORT ... Duration") y confirma que el Timeout de la función
(infra/template.yaml, 28s) y el LLM_REQUEST_TIMEOUT_SECONDS (8s por proveedor)
están desplegados — ver GUIA_DESPLIEGUE_Y_PRUEBAS.md, sección 0.1 y la tabla
de "Problemas comunes".

NOTA (septiembre 2026 — Cognito): desde que `/query` (y las rutas de FR5)
quedaron protegidas por el autorizador de Cognito de `infra/template.yaml`
(`JaverianaApi`), este script necesita un JWT válido en la variable de
entorno `COGNITO_ID_TOKEN` — sin eso, la API responde 401 antes de que la
petición llegue al Lambda (el rechazo lo hace API Gateway, no el código de
este proyecto). Ver GUIA_DESPLIEGUE_Y_PRUEBAS.md, sección 6.6, para
obtenerlo con `aws cognito-idp initiate-auth`.
"""
import json
import os
import sys

import requests


def main():
    if len(sys.argv) < 2:
        sys.exit(
            "Uso: python3 -m scripts.smoke_test_deployed_api <ApiUrl> [courseId] [timeout_seconds]"
        )

    id_token = os.getenv("COGNITO_ID_TOKEN")
    if not id_token:
        sys.exit(
            "Falta COGNITO_ID_TOKEN. Desde que la API quedó protegida por el "
            "autorizador de Cognito, este script necesita un JWT válido en "
            "esa variable de entorno — ver GUIA_DESPLIEGUE_Y_PRUEBAS.md, "
            "sección 6.6, para obtenerlo con `aws cognito-idp initiate-auth`."
        )

    api_url = sys.argv[1]
    course_id = sys.argv[2] if len(sys.argv) > 2 else "SMOKE-TEST"
    timeout_seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 45.0

    payload = {
        "studentId": "smoke-test-student",
        "courseId": course_id,
        "message": "¿Qué es el método ADD 3.0?",
    }
    headers = {"Authorization": f"Bearer {id_token}"}

    print(f"POST {api_url}\n{json.dumps(payload, ensure_ascii=False)}\n")
    print(
        f"(timeout del cliente: {timeout_seconds:.0f}s — la primera llamada "
        "puede tardar por el cold start de Lambda, es normal)\n"
    )
    response = requests.post(api_url, json=payload, headers=headers, timeout=timeout_seconds)
    print(f"HTTP {response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))

    if response.status_code == 200:
        data = response.json()
        if data.get("rag_chunks", 0) == 0 and not data.get("used_cag"):
            print(
                "\nNota: la respuesta no usó RAG ni CAG porque el curso "
                f"'{course_id}' no tiene material indexado todavía. Esto es "
                "esperado en un despliegue nuevo — confirma que el pipeline "
                "completo (Gateway -> Lambda -> Secrets Manager -> LLM) "
                "funciona igual. Ver la guía, sección 6, para precargar datos."
            )


if __name__ == "__main__":
    main()
