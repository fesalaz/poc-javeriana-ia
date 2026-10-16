"""
Registro de Artefactos Docentes (Iteraciones 1 y 4): valida el manifest y
mantiene el catálogo de artefactos por curso, junto con su estado de
disponibilidad (usado por el health checker de artefactos webhook).
"""
from typing import Dict, List

from .manifest import ArtifactManifest, validate_manifest


class ArtifactNotFoundError(Exception):
    pass


class ArtifactRegistry:
    def __init__(self):
        self._artifacts: Dict[str, ArtifactManifest] = {}
        self._available: Dict[str, bool] = {}

    def register_artifact(self, data: dict) -> ArtifactManifest:
        manifest = validate_manifest(data)
        self._artifacts[manifest.artifact_id] = manifest
        self._available[manifest.artifact_id] = True
        return manifest

    def get_artifact(self, artifact_id: str) -> ArtifactManifest:
        if artifact_id not in self._artifacts:
            raise ArtifactNotFoundError(artifact_id)
        return self._artifacts[artifact_id]

    def list_artifacts(self, course_id: str) -> List[ArtifactManifest]:
        return [a for a in self._artifacts.values() if a.course_id == course_id]

    def mark_unavailable(self, artifact_id: str) -> None:
        self._available[artifact_id] = False

    def mark_available(self, artifact_id: str) -> None:
        self._available[artifact_id] = True

    def is_available(self, artifact_id: str) -> bool:
        return self._available.get(artifact_id, False)
