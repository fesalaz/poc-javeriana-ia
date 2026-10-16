"""
Implementación REAL de la Caché KV para CAG sobre Amazon DynamoDB (ver
infra/template.yaml), con la misma interfaz que CAGStore (Iteración 1/5):
preload/get/has/invalidate. Usa el atributo `expires_at` (epoch en segundos)
para aprovechar el TTL nativo de DynamoDB ya configurado en la plantilla.

Requiere boto3 (incluido en requirements.txt) y la variable de entorno
DYNAMO_TABLE_NAME. No se ejecuta en el entorno de verificación de este
proyecto (no hay AWS ni boto3 instalado ahí) — la lógica de CAG se verifica
con CAGStore (en memoria), que implementa el mismo contrato.

NOTA (agosto 2026 — timeouts explícitos, ver GUIA_DESPLIEGUE_Y_PRUEBAS.md
sección 0.2): igual que el cliente de Secrets Manager en lambda_handler.py,
este cliente de DynamoDB fija connect/read timeout explícitos, como defensa
adicional por si una llamada real (`put_item`/`get_item`/`delete_item`) se
demora más de lo esperado — los defaults de botocore (60s) podrían agotar el
Timeout del Lambda antes de llegar a la cadena de proveedores LLM.
"""
import os
import time
from typing import Optional

try:
    import boto3
    from botocore.config import Config
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


class DynamoCAGStore:
    def __init__(self, table_name: str = None, region_name: str = None, clock=time.time):
        if not _AVAILABLE:
            raise ImportError("boto3 no está instalado. Ejecuta: pip install boto3")
        table_name = table_name or os.getenv("DYNAMO_TABLE_NAME")
        if not table_name:
            raise ValueError("Debes indicar table_name o definir DYNAMO_TABLE_NAME")
        config = Config(connect_timeout=5, read_timeout=8, retries={"max_attempts": 1})
        self._table = boto3.resource(
            "dynamodb", region_name=region_name, config=config
        ).Table(table_name)
        self._clock = clock

    def preload(self, course_id: str, context: str, ttl_seconds: Optional[float] = None,
                version: Optional[int] = None) -> None:
        item = {
            "course_id": course_id,
            "context": context,
            "version": version if version is not None else int(self._clock()),
        }
        if ttl_seconds:
            item["expires_at"] = int(self._clock() + ttl_seconds)
        self._table.put_item(Item=item)

    def get(self, course_id: str) -> Optional[str]:
        response = self._table.get_item(Key={"course_id": course_id})
        item = response.get("Item")
        if not item:
            return None
        expires_at = item.get("expires_at")
        if expires_at is not None and self._clock() > expires_at:
            # El TTL nativo de DynamoDB puede tardar hasta 48h en purgar el ítem;
            # se valida también aquí para no servir contexto vencido mientras tanto.
            return None
        return item.get("context")

    def has(self, course_id: str) -> bool:
        return self.get(course_id) is not None

    def invalidate(self, course_id: str) -> None:
        self._table.delete_item(Key={"course_id": course_id})
