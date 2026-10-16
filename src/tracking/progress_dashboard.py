"""Progress Dashboard API (Iteración 6, FR6): fachada de solo lectura sobre la
vista agregada — getProgress() para el estudiante, getCourseOverview() para el
profesor."""


class ProgressDashboardAPI:
    def __init__(self, aggregator):
        self.aggregator = aggregator

    def get_progress(self, student_id: str, course_id: str) -> dict:
        return self.aggregator.get_student_progress(course_id, student_id)

    def get_course_overview(self, course_id: str) -> dict:
        return self.aggregator.get_course_overview(course_id)
