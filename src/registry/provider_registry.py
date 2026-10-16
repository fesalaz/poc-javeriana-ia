"""
Provider Registry — materializa QAS-I1 (agregar un proveedor LLM es solo configuración,
sin tocar el Orquestador) y QAS-D1 (fallback automático entre proveedores mediante
circuit breaker por proveedor). Ver Iteraciones 1, 3 y 4 del diseño ADD 3.0.

NOTA (agosto 2026 — timeout de pared reforzado, ver GUIA_DESPLIEGUE_Y_PRUEBAS.md
sección 0.2): se confirmó en CloudWatch Logs del PoC desplegado que el
parámetro `timeout=` pasado a `ChatGoogleGenerativeAI` (src/adapters/gemini_adapter.py)
NO se respetó — la llamada a Gemini se quedó colgada hasta que el propio
Timeout de la función Lambda (28s) mató la ejecución (`Status: timeout` en
3 invocaciones consecutivas, cada una con `Duration: 28000.00 ms` exacto),
sin que el circuit breaker llegara a conmutar a los otros 2 proveedores. Esto
coincide con un bug conocido de langchain-google-genai
(github.com/langchain-ai/langchain-google/issues/731). Por eso el tope por
proveedor ya NO depende únicamente de que cada SDK respete su propio
`timeout=`: `ProviderRegistry._dispatch()` ejecuta cada llamada en un hilo
aparte y deja de esperarla tras `LLM_REQUEST_TIMEOUT_SECONDS` segundos,
cuente o no el SDK subyacente con su propio timeout. Es un mecanismo de
"dejar de esperar", no de cancelación real: en Python no se puede matar un
hilo bloqueado en I/O de red a la fuerza, así que el hilo huérfano sigue
corriendo en segundo plano hasta que la llamada de red subyacente termine o
el entorno de ejecución de Lambda se recicle — aceptable para un PoC, no para
producción a gran escala (ahí se preferiría un cliente HTTP con cancelación
real, p.ej. `httpx` con timeouts a nivel de conexión/lectura).
"""
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError
from dataclasses import dataclass
from typing import List, Optional

from ..adapters.base import GenerationResult, LLMProviderPort
from .circuit_breaker import CircuitBreaker


@dataclass
class ProviderEntry:
    adapter: LLMProviderPort
    priority: int
    breaker: CircuitBreaker


class AllProvidersFailedError(Exception):
    """Se lanza cuando ningún proveedor configurado pudo atender la petición."""


class ProviderRegistry:
    def __init__(self, per_call_timeout_seconds: Optional[float] = None):
        self._entries: List[ProviderEntry] = []
        # Tope de pared por llamada a proveedor. None/<=0 desactiva la
        # aplicación (útil en pruebas con MockLLMAdapter, que ya es
        # instantáneo). Por defecto, LLM_REQUEST_TIMEOUT_SECONDS (ver nota de
        # módulo arriba); se bajó de 8s a 6s para dejar margen dentro del
        # Timeout de Lambda (28s) considerando hasta 4 intentos secuenciales
        # en el peor caso por consulta (1 de embed() + 3 de generate()).
        self._per_call_timeout = (
            per_call_timeout_seconds
            if per_call_timeout_seconds is not None
            else float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "6"))
        )

    def register(self, adapter: LLMProviderPort, priority: int,
                  breaker: Optional[CircuitBreaker] = None) -> None:
        """Agregar un proveedor nuevo requiere SOLO esta llamada de configuración —
        no se modifica el Orquestador ni ningún otro componente (QAS-I1)."""
        self._entries.append(ProviderEntry(adapter, priority, breaker or CircuitBreaker()))
        self._entries.sort(key=lambda e: e.priority)

    def list_providers(self):
        return [(e.adapter.name, e.breaker.state.value) for e in self._entries]

    def get_adapter(self, name: str) -> LLMProviderPort:
        for entry in self._entries:
            if entry.adapter.name == name:
                return entry.adapter
        raise KeyError(f"proveedor no registrado: {name}")

    def _dispatch(self, method_name: str, *args, **kwargs):
        """Intenta `method_name` ("generate" o "embed") en cada proveedor por
        prioridad, con fallback y circuit breaker (QAS-D1).

        Distingue dos tipos de falla:
        - `NotImplementedError`: el proveedor no soporta esta operación (p.ej.
          un endpoint que no expone /embeddings). Es una limitación conocida
          de esa capacidad, NO una falla de disponibilidad del proveedor — así
          que no cuenta contra su circuit breaker (evita que llamar embed()
          repetidas veces abra por error el breaker que usa generate()).
        - Cualquier otra excepción: sí se registra como falla real del
          proveedor (auth, red, modelo deprecado, etc.) y sí cuenta contra su
          circuit breaker.

        Si todos fallan, el mensaje de error incluye el detalle de CADA
        proveedor (no solo el último) para no enmascarar la causa real."""
        errors = []
        for entry in self._entries:
            if not entry.breaker.allow_request():
                errors.append(f"{entry.adapter.name}: circuit breaker OPEN")
                continue
            try:
                result = self._call_with_wall_clock_timeout(
                    getattr(entry.adapter, method_name), *args, **kwargs
                )
                entry.breaker.record_success()
                return result
            except NotImplementedError as exc:
                errors.append(f"{entry.adapter.name}: no soporta '{method_name}' ({exc})")
                continue
            except Exception as exc:  # noqa: BLE001
                entry.breaker.record_failure()
                errors.append(f"{entry.adapter.name}: {exc}")
                continue
        detalle = "; ".join(errors) if errors else "no hay proveedores registrados"
        raise AllProvidersFailedError(
            f"todos los proveedores fallaron en '{method_name}'. Detalle por proveedor: {detalle}"
        )

    def _call_with_wall_clock_timeout(self, func, *args, **kwargs):
        """Ejecuta `func` y deja de esperarla tras `self._per_call_timeout`
        segundos, sin depender de que el proveedor/SDK subyacente respete su
        propio timeout (ver nota de módulo: caso real donde no lo hizo).
        Lanza `TimeoutError` (subclase de Exception, así que `_dispatch` la
        trata como falla real del proveedor, no como NotImplementedError)."""
        if not self._per_call_timeout or self._per_call_timeout <= 0:
            return func(*args, **kwargs)
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=self._per_call_timeout)
        except _FutureTimeoutError:
            raise TimeoutError(
                f"tiempo de espera agotado tras {self._per_call_timeout:.0f}s "
                "(sin respuesta del proveedor; el circuit breaker conmuta al "
                "siguiente en vez de seguir esperando)"
            )
        finally:
            # wait=False: no bloquear esperando a que termine el hilo huérfano
            # (si ya se agotó el timeout, seguir esperando aquí anularía el
            # propósito de este mecanismo). Ver nota de módulo sobre por qué
            # no se puede cancelar un hilo bloqueado en I/O de red en Python.
            executor.shutdown(wait=False)

    def generate(self, prompt: str, **params) -> GenerationResult:
        return self._dispatch("generate", prompt, **params)

    def embed(self, texts: List[str]) -> List[List[float]]:
        return self._dispatch("embed", texts)
