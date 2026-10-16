"""
Implementación REAL de CourseCatalogService sobre PostgreSQL (Iteración 6,
FR5), con la misma interfaz que la versión en memoria (`catalog.py`):
create_course/get_course/assign_teacher/enroll_student/list_courses/
is_enrolled_or_assigned.

Por qué existe: la versión en memoria (`CourseCatalogService`) es correcta
para pruebas y para `run_demo.py`, pero NO sirve detrás de un Lambda real —
cada invocación fría arranca con un catálogo vacío y las invocaciones
concurrentes ni siquiera comparten memoria entre sí. Mismo problema (y misma
solución, Ports & Adapters) que ya se resolvió para RAG (`PGVectorStore`) y
CAG (`DynamoCAGStore`): este adaptador persiste en la misma base de datos
PostgreSQL ya desplegada (`POSTGRES_URL`), en 3 tablas nuevas y sencillas
(`courses`, `course_teachers`, `course_students`) que se auto-crean si no
existen — no requiere ningún paso manual adicional (a diferencia de la
extensión `pgvector`, que sí lo requiere porque es una extensión del motor,
no una tabla de la aplicación).

Usa `psycopg` directamente (ya es una dependencia real del proyecto, para
PGVectorStore) en vez de SQLAlchemy/LangChain: es CRUD relacional simple, no
necesita la capa de embeddings/vector store de esas librerías, y evita sumar
otro import pesado al ya ajustado presupuesto de tiempo de cold start (ver
GUIA_DESPLIEGUE_Y_PRUEBAS.md, sección 0.2, sobre el costo de esos imports).

No se ejecuta en el entorno de verificación de este proyecto (no hay
PostgreSQL real ahí) — se prueba con `CourseCatalogService` (en memoria), que
implementa el mismo contrato, y los tests de `lambda_handler.py` inyectan esa
versión en memoria para probar el enrutamiento de las nuevas rutas HTTP.
"""
import os
from typing import List, Optional

from .catalog import Course, CourseNotFoundError, DuplicateCourseError

try:
    import psycopg
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def _to_psycopg_conninfo(postgres_url: str) -> str:
    """POSTGRES_URL usa el dialecto de SQLAlchemy (`postgresql+psycopg://...`,
    para que langchain-postgres/PGVector lo entienda); psycopg puro no conoce
    el sufijo `+psycopg`, así que se quita antes de conectar directamente."""
    return postgres_url.replace("postgresql+psycopg://", "postgresql://", 1)


class PostgresCourseCatalogService:
    def __init__(self, connection_string: Optional[str] = None):
        if not _AVAILABLE:
            raise ImportError("psycopg no está instalado. Ejecuta: pip install 'psycopg[binary]'")
        raw = connection_string or os.getenv(
            "POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/javeriana_ia?connect_timeout=5",
        )
        self._conninfo = _to_psycopg_conninfo(raw)
        self._ensure_schema()

    def _connect(self):
        conn = psycopg.connect(self._conninfo)
        conn.autocommit = True  # CRUD simple; evita manejar transacciones a mano en un PoC
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS courses ("
                "  course_id TEXT PRIMARY KEY,"
                "  name TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS course_teachers ("
                "  course_id TEXT NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,"
                "  teacher_id TEXT NOT NULL,"
                "  PRIMARY KEY (course_id, teacher_id)"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS course_students ("
                "  course_id TEXT NOT NULL REFERENCES courses(course_id) ON DELETE CASCADE,"
                "  student_id TEXT NOT NULL,"
                "  PRIMARY KEY (course_id, student_id)"
                ")"
            )

    def create_course(self, course_id: str, name: str) -> Course:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM courses WHERE course_id = %s", (course_id,)
            ).fetchone()
            if existing:
                raise DuplicateCourseError(course_id)
            conn.execute(
                "INSERT INTO courses (course_id, name) VALUES (%s, %s)", (course_id, name)
            )
        return Course(course_id=course_id, name=name)

    def get_course(self, course_id: str) -> Course:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT course_id, name FROM courses WHERE course_id = %s", (course_id,)
            ).fetchone()
            if not row:
                raise CourseNotFoundError(course_id)
            teachers = {
                r[0] for r in conn.execute(
                    "SELECT teacher_id FROM course_teachers WHERE course_id = %s", (course_id,)
                ).fetchall()
            }
            students = {
                r[0] for r in conn.execute(
                    "SELECT student_id FROM course_students WHERE course_id = %s", (course_id,)
                ).fetchall()
            }
        return Course(course_id=row[0], name=row[1], teachers=teachers, students=students)

    def assign_teacher(self, course_id: str, teacher_id: str) -> None:
        self.get_course(course_id)  # valida que el curso exista (lanza si no)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO course_teachers (course_id, teacher_id) VALUES (%s, %s) "
                "ON CONFLICT (course_id, teacher_id) DO NOTHING",
                (course_id, teacher_id),
            )

    def enroll_student(self, course_id: str, student_id: str) -> None:
        self.get_course(course_id)  # valida que el curso exista (lanza si no)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO course_students (course_id, student_id) VALUES (%s, %s) "
                "ON CONFLICT (course_id, student_id) DO NOTHING",
                (course_id, student_id),
            )

    def list_courses(self, user_id: str) -> List[Course]:
        with self._connect() as conn:
            course_ids = {
                r[0] for r in conn.execute(
                    "SELECT course_id FROM course_teachers WHERE teacher_id = %s", (user_id,)
                ).fetchall()
            } | {
                r[0] for r in conn.execute(
                    "SELECT course_id FROM course_students WHERE student_id = %s", (user_id,)
                ).fetchall()
            }
        return [self.get_course(cid) for cid in course_ids]

    def is_enrolled_or_assigned(self, user_id: str, course_id: str) -> bool:
        with self._connect() as conn:
            is_teacher = conn.execute(
                "SELECT 1 FROM course_teachers WHERE course_id = %s AND teacher_id = %s",
                (course_id, user_id),
            ).fetchone()
            if is_teacher:
                return True
            is_student = conn.execute(
                "SELECT 1 FROM course_students WHERE course_id = %s AND student_id = %s",
                (course_id, user_id),
            ).fetchone()
            return is_student is not None
