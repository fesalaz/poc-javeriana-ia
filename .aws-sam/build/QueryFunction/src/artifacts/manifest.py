"""
Manifest de artefacto docente (Iteración 4, QAS-I2). Se valida con reglas
explícitas (sin la librería `jsonschema`, no disponible en el entorno donde se
verificó esta PoC) antes de aceptar el registro — mismo espíritu de la táctica
"Manage Interfaces" ya documentada en el diseño.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

VALID_TYPES = {"embed", "webhook"}


class ManifestValidationError(Exception):
    """El manifest no cumple el contrato exigido por el Registro de Artefactos."""


@dataclass
class ArtifactManifest:
    artifact_id: str
    course_id: str
    name: str
    artifact_type: str  # "embed" | "webhook"
    url: str
    description: str = ""
    auth_secret_ref: Optional[str] = None  # referencia a Secrets Manager, nunca el secreto en sí
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    allowed_domains: Optional[List[str]] = None  # obligatorio para el modo "embed"


def validate_manifest(data: Dict[str, Any]) -> ArtifactManifest:
    required = ["artifact_id", "course_id", "name", "artifact_type", "url"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        raise ManifestValidationError(f"campos obligatorios faltantes: {missing}")

    artifact_type = data["artifact_type"]
    if artifact_type not in VALID_TYPES:
        raise ManifestValidationError(
            f"artifact_type inválido: {artifact_type!r} (válidos: {sorted(VALID_TYPES)})"
        )

    url = data["url"]
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ManifestValidationError("url debe ser http(s)")

    allowed_domains = data.get("allowed_domains")
    if artifact_type == "embed":
        if not allowed_domains:
            raise ManifestValidationError(
                "el modo 'embed' requiere 'allowed_domains' (whitelist de dominios, ver QAS-S2)"
            )
        host = urlparse(url).hostname or ""
        if not any(host == d or host.endswith("." + d) for d in allowed_domains):
            raise ManifestValidationError(
                f"el dominio de 'url' ({host!r}) no está en allowed_domains {allowed_domains}"
            )

    return ArtifactManifest(
        artifact_id=data["artifact_id"],
        course_id=data["course_id"],
        name=data["name"],
        artifact_type=artifact_type,
        url=url,
        description=data.get("description", ""),
        auth_secret_ref=data.get("auth_secret_ref"),
        input_schema=data.get("input_schema", {}),
        output_schema=data.get("output_schema", {}),
        allowed_domains=allowed_domains,
    )
