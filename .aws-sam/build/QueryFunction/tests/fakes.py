"""Dobles de prueba compartidos (no requieren red)."""


class FakeHttpResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class FakeHttpClient:
    """Simula un cliente HTTP (como `requests`) para probar ArtifactInvoker
    sin red. `should_fail=True` simula una excepción de conexión."""

    def __init__(self, status_code=200, json_data=None, should_fail=False):
        self.status_code = status_code
        self.json_data = json_data or {"reply": "respuesta del artefacto", "metadata": {}}
        self.should_fail = should_fail
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.should_fail:
            raise ConnectionError("fallo simulado de red hacia el artefacto")
        return FakeHttpResponse(status_code=self.status_code, json_data=self.json_data)
