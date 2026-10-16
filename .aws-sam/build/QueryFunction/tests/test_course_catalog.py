"""Verifica el Course Catalog Service (Iteración 6, FR5)."""
import unittest

from src.courses.catalog import CourseCatalogService, CourseNotFoundError, DuplicateCourseError


class TestCourseCatalog(unittest.TestCase):
    def setUp(self):
        self.catalog = CourseCatalogService()
        self.catalog.create_course("ISIS-1234", "Arquitectura de Software")

    def test_create_duplicate_raises(self):
        with self.assertRaises(DuplicateCourseError):
            self.catalog.create_course("ISIS-1234", "otro nombre")

    def test_get_missing_course_raises(self):
        with self.assertRaises(CourseNotFoundError):
            self.catalog.get_course("NO-EXISTE")

    def test_enroll_and_assign(self):
        self.catalog.enroll_student("ISIS-1234", "est-1")
        self.catalog.assign_teacher("ISIS-1234", "prof-1")
        course = self.catalog.get_course("ISIS-1234")
        self.assertIn("est-1", course.students)
        self.assertIn("prof-1", course.teachers)

    def test_list_courses_for_user(self):
        self.catalog.create_course("ISIS-5678", "Bases de Datos")
        self.catalog.enroll_student("ISIS-1234", "est-1")
        self.catalog.enroll_student("ISIS-5678", "est-1")

        courses = self.catalog.list_courses("est-1")
        self.assertEqual({c.course_id for c in courses}, {"ISIS-1234", "ISIS-5678"})

    def test_is_enrolled_or_assigned(self):
        self.catalog.enroll_student("ISIS-1234", "est-1")
        self.assertTrue(self.catalog.is_enrolled_or_assigned("est-1", "ISIS-1234"))
        self.assertFalse(self.catalog.is_enrolled_or_assigned("est-2", "ISIS-1234"))
        self.assertFalse(self.catalog.is_enrolled_or_assigned("est-1", "CURSO-INEXISTENTE"))


if __name__ == "__main__":
    unittest.main()
