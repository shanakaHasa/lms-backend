"""Storage adapters, one module per aggregate.

Services depend on the `*Repository` protocols in these modules, never on
`AsyncSession`. That indirection buys two things worth the extra layer:

1. **The service layer is testable with no database.** Phase 1 builds every
   feature through the agent before a migration is ever applied, so without this
   the business rules would go unverified until the very end.
2. **Tenant scoping is structural.** `TenantScopedRepository` takes the tenant
   at construction and no method accepts one, so a query that forgets to filter
   by tenant is not something review has to catch — it cannot be written.
"""

from app.repositories.base import TenantScopedRepository, translate_integrity_error
from app.repositories.course import CourseRepository, SqlCourseRepository
from app.repositories.enrolment import EnrolmentRepository, SqlEnrolmentRepository
from app.repositories.student import SqlStudentRepository, StudentRepository

__all__ = [
    "CourseRepository",
    "EnrolmentRepository",
    "SqlCourseRepository",
    "SqlEnrolmentRepository",
    "SqlStudentRepository",
    "StudentRepository",
    "TenantScopedRepository",
    "translate_integrity_error",
]
