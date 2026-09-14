"""Aggregates every /api/v1 router.

Routers are added as each step lands:
  B3  students, courses, enrolments
  B4  materials (upload, confirm, status, reindex)
  B5  search
  B6  chat (SSE)
  B7  proposals (approve, reject, list)
"""

from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")
