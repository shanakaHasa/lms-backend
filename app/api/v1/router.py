"""Aggregates every /api/v1 router.

Routers are added as each step lands:
  B3  students, courses, enrolments  ← done
  B4  materials (upload, confirm, status, reindex)
  B5  search
  B6  chat (SSE)
  B7  proposals (approve, reject, list)

Every route registered here is covered by the tenant-isolation matrix in
`tests/unit/test_tenant_isolation.py`, and a meta-test in that module fails the
suite when one is not — so adding a router without adding its isolation case is
not something that can pass review quietly.
"""

from fastapi import APIRouter

from app.api.v1 import courses, enrolments, students

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(students.router)
api_router.include_router(courses.router)
api_router.include_router(enrolments.router)
