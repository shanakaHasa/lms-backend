"""Business rules, independent of FastAPI.

Nothing in this package imports from `fastapi`. That is what lets the CLI, the
eval runner, the MCP server and — from step B6 — the agent's tools drive exactly
the same code the HTTP layer does, including the scope checks, which live here
rather than in route decorators for precisely that reason.
"""

from app.services.audit import AuditSink, SqlAuditSink
from app.services.course_service import CourseService
from app.services.enrolment_service import EnrolmentService
from app.services.student_service import StudentService

__all__ = [
    "AuditSink",
    "CourseService",
    "EnrolmentService",
    "SqlAuditSink",
    "StudentService",
]
