"""One module per table.

Importing this package imports every model, which is what populates
`Base.metadata` — Alembic, the drift checker and the schema tests all depend on
that, so `import app.models` is enough.

Dependency rule: model modules import from `app.models.base` and never from each
other at runtime. A relationship's type annotation goes under `if TYPE_CHECKING`
and SQLAlchemy resolves it from the class name.
"""

from app.models.action_proposal import ActionProposal
from app.models.audit_event import AuditEvent
from app.models.base import Base, TimestampMixin, uuid_pk
from app.models.conversation import Conversation
from app.models.course import Course
from app.models.course_material import CourseMaterial
from app.models.enrolment import Enrolment
from app.models.ingestion_job import IngestionJob
from app.models.material_chunk import MaterialChunk
from app.models.message import Message
from app.models.student import Student

__all__ = [
    "ActionProposal",
    "AuditEvent",
    "Base",
    "Conversation",
    "Course",
    "CourseMaterial",
    "Enrolment",
    "IngestionJob",
    "MaterialChunk",
    "Message",
    "Student",
    "TimestampMixin",
    "uuid_pk",
]
