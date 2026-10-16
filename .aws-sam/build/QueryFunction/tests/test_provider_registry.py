"""Verifica el fallback entre proveedores y que el circuit breaker evita llamar a
un proveedor OPEN (Iteración 3/4, QAS-D1 y QAS-I1)."""
import time
import unittest
from typing import List

from src.adapters.base import GenerationResult, HealthStatus, LLMProviderPort
from src.adapters.mock_adapter import MockLLMAdapter
from src.registry.circuit_breaker import CircuitBreaker
from src.registry.provider_registry import AllProvidersFailedError, ProviderRegistry


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class HangingAdapter(LLMProviderPort):
    """Simula el bug real observado en producción: un proveedor cuyo SDK NO
    respeta ningún timeout propio (ver la nota en gemini_adapter.py sobre
    ChatGoogleGenerativeAI/langchain-google issue #731) y se queda colgado
    `hang_seconds`. Se usa para probar el timeout de PARED que aplica
    `ProviderRegistry` por su cuenta, sin depender del adaptador."""

    def __init__(self, name: str, hang_seconds: float):
        self.name = name
        self.hang_seconds = hang_seconds
        self.call_count = 0

    def generate(self, prompt: str, **params) -> GenerationResult:
        self.call_count += 1
        time.sleep(self.hang_seconds)
        return GenerationResult(text="nunca debería llegar aquí en la prueba", provider=self.name, latency_ms=0)

    def embed(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError

    def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)


class TestProviderRegistry(unittest.TestCase):
    def test_falls_back_to_secondary_when_primary_fails(self):
        registry = ProviderRegistry()
        primary = MockLLMAdapter(name="gemini", healthy=False)
        secondary = MockLLMAdapter(name="llama", healthy=True)
        registry.register(primary, priority=1)
        registry.register(secondary, priority=2)

        result = registry.generate("hola")
        self.assertEqual(result.provider, "llama")

    def test_breaker_skips_open_provider_without_calling_it(self):
        clock = FakeClock()
        registry = ProviderRegistry()
        primary = MockLLMAdapter(name="gemini", healthy=False)
        secondary = MockLLMAdapter(name="llama", healthy=True)
        registry.register(primary, priority=1, breaker=CircuitBreaker(failure_threshold=1, clock=clock))
        registry.register(secondary, priority=2, breaker=CircuitBreaker(clock=clock))

        registry.generate("uno")  # abre el breaker de "gemini"
        calls_before = primary.call_count
        registry.generate("dos")  # no debería volver a invocar a "gemini" (OPEN)
        self.assertEqual(primary.call_count, calls_before)

    def test_all_providers_failed_raises(self):
        registry = ProviderRegistry()
        registry.register(MockLLMAdapter(name="gemini", healthy=False), priority=1)
        registry.register(MockLLMAdapter(name="llama", healthy=False), priority=2)
        with self.assertRaises(AllProvidersFailedError):
            registry.generate("hola")

    def test_new_provider_is_only_configuration_qas_i1(self):
        """Agregar un proveedor nuevo no requiere tocar ProviderRegistry ni el
        Orquestador: basta con implementar LLMProviderPort y registrarlo."""
        registry = ProviderRegistry()
        registry.register(MockLLMAdapter(name="proveedor_futuro"), priority=1)
        result = registry.generate("prueba")
        self.assertEqual(result.provider, "proveedor_futuro")

    def test_embed_falls_back_past_providers_without_embeddings_support(self):
        """Reproduce el caso real: 2 de 3 proveedores no exponen /embeddings
        (Groq/OpenRouter en este proyecto) y deben saltarse sin tumbar todo el
        fallback ni ocultar el error del que sí lo soporta."""
        registry = ProviderRegistry()
        sin_embeddings_1 = MockLLMAdapter(name="openai", supports_embed=False)
        sin_embeddings_2 = MockLLMAdapter(name="llama", supports_embed=False)
        con_embeddings = MockLLMAdapter(name="gemini", supports_embed=True)
        registry.register(sin_embeddings_1, priority=1)
        registry.register(sin_embeddings_2, priority=2)
        registry.register(con_embeddings, priority=3)

        vectors = registry.embed(["hola"])
        self.assertEqual(len(vectors), 1)

    def test_embed_not_implemented_does_not_open_breaker_for_generate(self):
        """Bug real detectado: llamar embed() en un proveedor que no lo
        soporta NO debe contar como falla de su circuit breaker — ese
        breaker también se usa para generate(), y no es una falla de
        disponibilidad, es una capacidad que el proveedor nunca tuvo."""
        registry = ProviderRegistry()
        adapter = MockLLMAdapter(name="openai", supports_embed=False)
        breaker = CircuitBreaker(failure_threshold=2)
        registry.register(adapter, priority=1, breaker=breaker)

        for _ in range(5):
            with self.assertRaises(AllProvidersFailedError):
                registry.embed(["hola"])

        self.assertEqual(breaker.state.value, "CLOSED")
        result = registry.generate("¿sigue funcionando generate()?")
        self.assertEqual(result.provider, "openai")

    def test_all_providers_failed_message_includes_every_provider(self):
        """El mensaje de error debe mostrar la causa de CADA proveedor, no
        solo la del último — de lo contrario un NotImplementedError de un
        proveedor de respaldo puede enmascarar el error real de otro."""
        registry = ProviderRegistry()
        registry.register(MockLLMAdapter(name="openai", supports_embed=False), priority=1)
        registry.register(MockLLMAdapter(name="llama", supports_embed=False), priority=2)
        registry.register(MockLLMAdapter(name="gemini", healthy=False), priority=3)

        with self.assertRaises(AllProvidersFailedError) as ctx:
            registry.embed(["hola"])
        message = str(ctx.exception)
        self.assertIn("openai", message)
        self.assertIn("llama", message)
        self.assertIn("gemini", message)

    def test_wall_clock_timeout_falls_back_even_if_provider_never_raises(self):
        """Reproduce el caso real detectado en CloudWatch Logs: un proveedor
        que se queda colgado mucho más de lo esperado (su SDK no respeta su
        propio timeout) no debe bloquear el fallback — ProviderRegistry deja
        de esperarlo por su cuenta, con un timeout de pared independiente del
        adaptador."""
        registry = ProviderRegistry(per_call_timeout_seconds=0.05)
        hanging = HangingAdapter(name="gemini", hang_seconds=5.0)  # simula 28s reales
        healthy = MockLLMAdapter(name="openai", healthy=True)
        registry.register(hanging, priority=1)
        registry.register(healthy, priority=2)

        start = time.monotonic()
        result = registry.generate("hola")
        elapsed = time.monotonic() - start

        self.assertEqual(result.provider, "openai")
        # La prueba debe resolverse cerca del timeout configurado (0.05s),
        # NO cerca de los 5s que el proveedor colgado tarda en su hilo huérfano.
        self.assertLess(elapsed, 1.0)

    def test_wall_clock_timeout_counts_as_failure_against_circuit_breaker(self):
        """Un proveedor que se cuelga y se corta por el timeout de pared debe
        contar como falla real (a diferencia de NotImplementedError), para
        que el circuit breaker eventualmente lo saque de la rotación."""
        registry = ProviderRegistry(per_call_timeout_seconds=0.05)
        hanging = HangingAdapter(name="gemini", hang_seconds=5.0)
        breaker = CircuitBreaker(failure_threshold=1)
        registry.register(hanging, priority=1, breaker=breaker)
        registry.register(MockLLMAdapter(name="openai", healthy=True), priority=2)

        registry.generate("uno")
        self.assertEqual(breaker.state.value, "OPEN")

    def test_embed_has_no_real_fallback_when_only_gemini_supports_it(self):
        """Reproduce el escenario EXACTO detectado en la prueba de carga real
        de 100 usuarios concurrentes (septiembre 2026, hallazgo R-7 de
        EVALUACION_ATAM_LITE.md), antes de la corrección: con el circuit
        breaker de "gemini" abierto y sin ningún otro proveedor de generate()
        que implemente embed(), TODO el pipeline RAG falla — no hay a dónde
        conmutar. Esta prueba documenta el problema original; la siguiente
        (`test_embed_falls_back_to_bedrock_and_voyage_after_gemini_fix`)
        prueba la corrección."""
        registry = ProviderRegistry()
        gemini = MockLLMAdapter(name="gemini", supports_embed=True, healthy=False)
        openai = MockLLMAdapter(name="openai", supports_embed=False)
        llama = MockLLMAdapter(name="llama", supports_embed=False)
        registry.register(gemini, priority=1)
        registry.register(openai, priority=2)
        registry.register(llama, priority=3)

        with self.assertRaises(AllProvidersFailedError) as ctx:
            registry.embed(["¿qué es RAG?"])
        message = str(ctx.exception)
        self.assertIn("gemini:", message)
        self.assertIn("openai: no soporta 'embed'", message)
        self.assertIn("llama: no soporta 'embed'", message)

    def test_embed_falls_back_to_bedrock_and_voyage_after_gemini_fix(self):
        """Corrección de R-7 (septiembre 2026): con AWS Bedrock (respaldo 1) y
        Voyage AI (respaldo 2) registrados detrás de Gemini, el mismo
        escenario del test anterior (gemini con el breaker abierto, openai/
        llama sin soporte de embed()) ya tiene a dónde conmutar."""
        registry = ProviderRegistry()
        gemini = MockLLMAdapter(name="gemini", supports_embed=True, healthy=False)
        openai = MockLLMAdapter(name="openai", supports_embed=False)
        llama = MockLLMAdapter(name="llama", supports_embed=False)
        bedrock = MockLLMAdapter(name="bedrock", supports_embed=True)
        voyage = MockLLMAdapter(name="voyage", supports_embed=True)
        registry.register(gemini, priority=1)
        registry.register(openai, priority=2)
        registry.register(llama, priority=3)
        registry.register(bedrock, priority=4)
        registry.register(voyage, priority=5)

        # Mismo escenario que el test anterior (gemini caído, openai/llama sin
        # soporte de embed()) pero ahora SÍ resuelve, sin lanzar AllProvidersFailedError.
        vectors = registry.embed(["¿qué es RAG?"])
        self.assertEqual(len(vectors), 1)
        self.assertEqual(bedrock.embed_call_count, 1)  # confirma que Bedrock (respaldo 1) fue quien resolvió la llamada
        self.assertEqual(voyage.embed_call_count, 0)  # y que no fue necesario llegar a Voyage (respaldo 2)

    def test_embed_falls_back_to_voyage_when_bedrock_also_fails(self):
        """Si además Bedrock (respaldo 1) falla, debe conmutar a Voyage
        (respaldo 2) — la cadena de embed() tiene 2 niveles de redundancia
        real detrás de Gemini, no solo 1."""
        registry = ProviderRegistry()
        registry.register(MockLLMAdapter(name="gemini", supports_embed=True, healthy=False), priority=1)
        registry.register(MockLLMAdapter(name="bedrock", supports_embed=True, healthy=False), priority=4)
        registry.register(MockLLMAdapter(name="voyage", supports_embed=True, healthy=True), priority=5)

        vectors = registry.embed(["¿qué es RAG?"])
        self.assertEqual(len(vectors), 1)

    def test_generate_chain_unchanged_bedrock_and_voyage_are_skipped(self):
        """Bedrock y Voyage solo implementan embed() (ver adapters/*.py) — no
        deben participar en la cadena de generate(), que sigue siendo
        Gemini -> OpenAI/Groq -> Llama/OpenRouter sin cambios. Se fuerza a
        que los 3 proveedores de generate() fallen para comprobar que
        Bedrock/Voyage de verdad se saltan (NotImplementedError, sin contar
        contra su circuit breaker) en vez de "resolver" generate() por error."""
        registry = ProviderRegistry()
        registry.register(MockLLMAdapter(name="gemini", healthy=False), priority=1)
        registry.register(MockLLMAdapter(name="openai", healthy=False), priority=2)
        registry.register(MockLLMAdapter(name="llama", healthy=False), priority=3)
        registry.register(MockLLMAdapter(name="bedrock", supports_generate=False), priority=4)
        registry.register(MockLLMAdapter(name="voyage", supports_generate=False), priority=5)

        with self.assertRaises(AllProvidersFailedError) as ctx:
            registry.generate("¿qué es RAG?")
        message = str(ctx.exception)
        self.assertIn("bedrock: no soporta 'generate'", message)
        self.assertIn("voyage: no soporta 'generate'", message)

    def test_wall_clock_timeout_disabled_when_zero_or_none(self):
        """Con per_call_timeout_seconds<=0 (o None), no se aplica ningún
        tope de pared — comportamiento previo, usado por defecto con
        MockLLMAdapter en el resto de las pruebas de este archivo."""
        registry = ProviderRegistry(per_call_timeout_seconds=0)
        adapter = MockLLMAdapter(name="gemini", healthy=True)
        registry.register(adapter, priority=1)
        result = registry.generate("hola")
        self.assertEqual(result.provider, "gemini")


if __name__ == "__main__":
    unittest.main()
