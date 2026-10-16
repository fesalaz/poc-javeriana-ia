"""
Invocador de artefactos docentes (Iteración 4). Para el modo 'webhook' llama al
endpoint externo con el contrato estándar {studentId, courseId, message} ->
{reply, metadata}; para el modo 'embed' solo entrega la información necesaria
para incrustar el iframe (la restricción por whitelist ya se validó al
registrar el manifest, ver manifest.py).

Si la invocación falla, el artefacto se marca 'no disponible' en el Registro
(mismo espíritu de "Fault Detection" de la Iteración 3), sin afectar al resto
del sistema.

`http_client` es cualquier objeto con `.post(url, json=..., timeout=...)` que
devuelva algo con `.status_code` y `.json()` — en producción, el módulo
`requests`; en pruebas, un doble de prueba (ver tests/fakes.py).
"""
import time
from typing import Any, Dict, Optional

from .registry import ArtifactRegistry


class ArtifactInvocationError(Exception):
    pass


class ArtifactInvoker:
    def __init__(self, registry: ArtifactRegistry, http_client=None, activity_logger=None):
        self.registry = registry
        self.http_client = http_client
        self.activity_logger = activity_logger

    def invoke(self, artifact_id: str, student_id: str, course_id: str, message: str) -> Dict[str, Any]:
        manifest = self.registry.get_artifact(artifact_id)
        if manifest.artifact_type != "webhook":
            raise ArtifactInvocationError(
                f"el artefacto {artifact_id} es de tipo {manifest.artifact_type!r}, no 'webhook'"
            )
        if not self.registry.is_available(artifact_id):
            raise ArtifactInvocationError(f"artefacto {artifact_id} marcado como no disponible")
        if self.http_client is None:
            raise ArtifactInvocationError("no se configuró un http_client para invocar artefactos")

        payload = {"studentId": student_id, "courseId": course_id, "message": message}
        start = time.monotonic()
        try:
            response = self.http_client.post(manifest.url, json=payload, timeout=8)
            if response.status_code >= 400:
                raise ArtifactInvocationError(f"respuesta HTTP {response.status_code}")
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            self.registry.mark_unavailable(artifact_id)
            if self.activity_logger:
                self.activity_logger.log_event(
                    student_id, course_id, "artifact_invocation_failed",
                    {"artifact_id": artifact_id, "error": str(exc)},
                )
            raise ArtifactInvocationError(str(exc)) from exc

        latency_ms = (time.monotonic() - start) * 1000
        if self.activity_logger:
            self.activity_logger.log_event(
                student_id, course_id, "artifact_invoked",
                {"artifact_id": artifact_id, "latency_ms": latency_ms},
            )
        return {"reply": data.get("reply", ""), "metadata": data.get("metadata", {})}

    def get_embed_info(self, artifact_id: str) -> Dict[str, Any]:
        manifest = self.registry.get_artifact(artifact_id)
        if manifest.artifact_type != "embed":
            raise ArtifactInvocationError(
                f"el artefacto {artifact_id} es de tipo {manifest.artifact_type!r}, no 'embed'"
            )
        return {"url": manifest.url, "allowed_domains": manifest.allowed_domains}
