# backend

The LMS service. It owns students, courses, enrolments and course materials;
the retrieval pipeline over those materials; and the assistant that can read
them and **propose** changes a human approves.

It does not own user accounts, passwords or signing keys — those belong to
`auth-backend`. This service **verifies** tokens and has no code path that can
mint one. That asymmetry is the boundary.

## Quick start

```bash
make venv
cp .env.example .env      # DATABASE_URL from ../platform; AUTH_DISABLED stays true for now
make api                  # http://localhost:8000/docs
```

```bash
make check                # lint + typecheck + drift + unit tests — what CI runs
make test-unit            # fast, no database
make drift                # model DDL vs migration DDL, no database needed
make db-check             # is the database reachable, and how slow is it
```

Extras install per step, so early work is not blocked on unverifiable
dependencies: `make venv-rag` at B5, `make venv-agent` at B6.

## Build order

| # | Step | Done when |
| --- | --- | --- |
| B1 | Skeleton | `/healthz` 200; lint, types and tests clean ← **you are here** |
| B2 | LMS schema, migration `0001` | Drift check clean |
| B3 | Students, courses, enrolments CRUD | **A teacher can create and enrol a student via `/docs`** |
| B4 | Materials + ingestion worker | A syllabus reaches `indexed` |
| B5 | Retrieval | `/search` returns cited passages |
| B6 | Agent, read-only | A question is answered with citations, on both providers |
| B7 | Writes + approval gate | **Ask it to enrol someone → proposal → approve → the row exists** |

## Decisions worth knowing before changing something

**Auth is stubbed, deliberately.** `AUTH_DISABLED=true` yields a fixed
`Principal`. Every route and tool already depends on it, so switching to real
tokens changes configuration, not structure. The startup log says so loudly, and
`Settings` refuses to boot in production that way.

**Verification is offline.** Tokens are checked against cached JWKS, so a normal
request calls auth zero times. The cost: a token cannot be withdrawn mid-life.
Compensating controls are short lifetimes and, later, the revocation feed.
`readyz` reports auth as information only — wiring it into readiness would turn
an auth blip into a task-recycling storm at the moment auth is already
struggling.

**The JWKS refetch is rate-limited.** Refetching once on an unknown `kid` is the
key-rotation path and must work without a deploy. But without a negative cache
and a per-process cooldown, tokens carrying random `kid` values turn this
service into a denial-of-service amplifier aimed at our own identity provider.
`tests/unit/test_jwks_cache.py` pins that: ten bogus key ids must produce one
refresh, not ten.

**`aud` is verified.** The reference scaffold this replaces passed
`options={"verify_aud": False}`. Carrying that across would let a token minted
for auth's own admin API be replayed here.

**Storage and the queue are interfaces.** `LocalStorage` and a Postgres queue
(`SELECT … FOR UPDATE SKIP LOCKED`) today; S3 and SQS later, behind the same
protocols. The worker semantics — idempotency, retries, visibility timeout,
dead-letter — are identical either way, which is what makes the swap a day's
work rather than a rewrite.

**Chunks are written to Postgres before vectors go to Pinecone.** Dying between
the two leaves chunks with no vectors: invisible to dense search, recoverable by
a reindex. The reverse order leaves vectors pointing at rows that do not exist,
which a user sees as a citation to a document nobody can open.

**Redaction covers secrets *and* student personal information**, and is a
structlog processor so it applies to every log call rather than depending on
each caller. It is a safety net, not de-identification — the primary control is
not putting student records into traces at all.

## Layout

```
app/core/       config · db · logging · redaction · errors · middleware · security · deps
app/models/     one module per table
app/schemas/    Pydantic request/response — the API contract
app/api/v1/     HTTP only, thin, no business logic
app/services/   All the logic. No FastAPI imports, so the CLI, the eval runner
                and the MCP server drive the same code the HTTP layer does.
app/rag/        embeddings · vector store · keyword store · rerank · retriever
app/ingestion/  storage · queue · parser · chunker · worker
app/agent/      LangGraph graph, tools, the approval gate
```

Dependencies point one way: `api → services → models`, `core` underneath.
