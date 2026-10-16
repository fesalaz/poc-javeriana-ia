"""
Course Catalog Service (Iteración 6, FR5): cursos, inscripción de estudiantes y
asignación de docentes. `course_id` es la clave de partición usada en todo el
sistema (retrieval, CAG, seguimiento) y también el campo formalizado como
control de autorización en la Iteración 7 (ver is_enrolled_or_assigned).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Set


class CourseNotFoundError(Exception):
    pass


class DuplicateCourseError(Exception):
    pass


@dataclass
class Course:
    course_id: str
    name: str
    teachers: Set[str] = field(default_factory=set)
    students: Set[str] = field(default_factory=set)


class CourseCatalogService:
    def __init__(self):
        self._courses: Dict[str, Course] = {}

    def create_course(self, course_id: str, name: str) -> Course:
        if course_id in self._courses:
            raise DuplicateCourseError(course_id)
        course = Course(course_id=course_id, name=name)
        self._courses[course_id] = course
        return course

    def get_course(self, course_id: str) -> Course:
        if course_id not in self._courses:
            raise CourseNotFoundError(course_id)
        return self._courses[course_id]

    def assign_teacher(self, course_id: str, teacher_id: str) -> None:
        self.get_course(course_id).teachers.add(teacher_id)

    def enroll_student(self, course_id: str, student_id: str) -> None:
        self.get_course(course_id).students.add(student_id)

    def list_courses(self, user_id: str) -> List[Course]:
        return [
            c for c in self._courses.values()
            if user_id in c.students or user_id in c.teachers
        ]

    def is_enrolled_or_assigned(self, user_id: str, course_id: str) -> bool:
        """Control de autorización formalizado en la Iteración 7: un usuario
        solo puede operar sobre cursos a los que pertenece."""
        course = self._courses.get(course_id)
        if not course:
            return False
        return user_id in course.students or user_id in course.teachers
