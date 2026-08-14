# edugen.live

A quiz platform where questions are generated **only from retrieved NCERT
curriculum content** — never from the model's general knowledge — and each
student's per-topic proficiency score, updated after every answer,
deterministically drives the difficulty of their next quiz.

```
┌──────────────┐        ┌───────────────────────────────────────────────┐
│  Next.js     │  JWT   │  FastAPI                                      │
│  frontend    │───────▶│                                               │
│              │        │  POST /quizzes                                │
│  subjects    │        │    1. read proficiency(user, subject, topic)  │
│  quiz        │        │    2. score ──▶ difficulty mix   [pure fn]    │
│  results     │        │    3. refuse if topic has no content          │
│  proficiency │        │    4. retrieve top-k chunks  ── WHERE topic   │
└──────────────┘        │    5. cache lookup ─ hit? ──▶ serve           │
                        │    6. Gemini generate, prompt = chunks only   │
                        │    7. validate every source_chunk_id          │
                        │    8. persist quiz + questions                │
                        └────────────────┬──────────────────────────────┘
                                         │
                              ┌──────────▼───────────┐
                              │ PostgreSQL + pgvector│
                              │  users  subjects     │
                              │  content_chunks(vec) │
                              │  quizzes questions   │
                              │  attempts proficiency│
                              │  generation_cache    │
                              └──────────▲───────────┘
                                         │
                       ┌─────────────────┴──────────────────┐
                       │ scripts/ingest.py                  │
                       │ extract → chunk → embed → store    │
                       │ (a script, not Airflow — see below)│
                       └────────────────────────────────────┘
```

## The two things this project actually proves

**1. Curriculum-grounded generation.** Every question stores the ids of the
content chunks it was generated from, and three layers enforce it:

- retrieval filters to the requested `(subject, topic)` **in SQL, before**
  vector ranking, so another chapter's text physically cannot enter the prompt;
- the prompt constrains the model to the numbered excerpts and requires it to
  cite the excerpt ids it used;
- `validate_generated_questions` re-checks every citation against the ids that
  were actually retrieved. A question citing anything else raises
  `UngroundedGenerationError` — the generation is refused, not trimmed and
  served.

A topic with no ingested content returns **422**, never an ungrounded quiz.

**2. Deterministic, explainable adaptive selection.** Proficiency is an
exponentially-weighted moving average in `[0, 1]`, starting at `0.50`:

```
score ← score + 0.30 × (target − score)

target      correct   incorrect
  easy        0.60      0.00
  medium      0.80      0.20
  hard        1.00      0.40

band:  score < 0.40 → easy    < 0.70 → medium    else → hard
```

The band maps to a fixed difficulty mix, allocated across the quiz by the
largest-remainder method. Everything in `app/services/proficiency.py` is a pure
function — no database, no clock, no randomness — which is why the test can
assert exact scores for a scripted answer sequence.

Full derivation and the reasoning behind each constant: **[DESIGN.md](DESIGN.md)**.

## Content scope — a deliberate decision

The ingested corpus is **2 subjects × 2 grades × 3 chapters = 12 chapters,
79 indexed passages** (Science and Mathematics, Grades 8 and 10), stored as
NCERT-aligned chapter text in `backend/content/`.

This is a scoping decision, not a limitation of the architecture. The
retrieval → generation → adaptive pipeline is fully general; full Grades 1–12
coverage is a data-acquisition and licensing problem, not an engineering one,
and would add ingestion volume rather than a new technical idea. The ingestion
script already accepts PDFs (`pypdf`) alongside text, so dropping real NCERT
chapter PDFs into a content subdirectory works without a code change.

## Quick start

### Docker (everything, including ingestion)

```bash
docker compose up --build
```

Frontend on http://localhost:3000, API docs on http://localhost:8000/docs,
Postgres on port 5433. The `ingest` service runs once and exits after loading
the corpus.

This works with no API key: `LLM_PROVIDER` defaults to `stub`, an explicitly
opt-in offline generator that still builds every question out of retrieved
chunk text. To use the real provider, create a `.env` next to
`docker-compose.yml`:

```
GEMINI_API_KEY=your-key
LLM_PROVIDER=gemini
EMBEDDING_PROVIDER=gemini
```

Switching `EMBEDDING_PROVIDER` requires re-running ingestion:

```bash
docker compose run --rm ingest python -m scripts.ingest --force
```

### Local development

```bash
docker compose up -d db

cd backend
python -m venv .venv && .venv/Scripts/activate      # source .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt
cp .env.example .env
python -m scripts.ingest
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

## Tests

```bash
cd backend
pytest -q
```

Logic tests run with no database. The integration test needs Postgres with
pgvector and is skipped unless `TEST_DATABASE_URL` is set.

**Point it at a separate database.** The integration fixture truncates every
table before seeding, so aiming it at `edugen` would wipe the ingested demo
corpus. Create the test database once:

```bash
docker compose exec -T db createdb -U edugen edugen_test
```

then:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://edugen:edugen@localhost:5433/edugen_test pytest -q
```

| Test file | What it pins down |
|---|---|
| `test_proficiency.py` | Scripted answer sequence → exact scores, exact bands, exact next-quiz mix; band boundaries; monotonicity |
| `test_grounding.py` | A question citing an unretrieved chunk is rejected; malformed questions dropped with a reason; empty context never reaches the model |
| `test_ingestion_and_retrieval.py` | Chunking determinism, corpus is the documented scope, the embedder ranks the right chapter first |
| `test_integration_quiz_flow.py` | quiz → answer → proficiency update → next quiz reflects it; no-content topic returns 422; cache hit is recorded; answering twice does not double-count |

CI runs lint + the full suite (including integration, against a pgvector
service container) plus a frontend typecheck and build on every push.

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/auth/register`, `/auth/login` | JWT bearer tokens |
| `GET` | `/auth/me` | |
| `GET` | `/subjects` | Built from `content_chunks`, so it can only list groundable topics |
| `POST` | `/quizzes` | Retrieval + generation; difficulty from current proficiency |
| `GET` | `/quizzes/{id}` | Student view — never includes the answer key |
| `POST` | `/quizzes/{id}/answer` | Records the attempt, updates proficiency (idempotent per question) |
| `GET` | `/quizzes/{id}/results` | Scores, source passages, next-quiz mix; keys revealed only for answered questions |
| `GET` | `/users/{id}/proficiency` | Per-topic scores and the formula itself |
| `GET` | `/cache/stats` | Measured lookups / hits / hit rate |
| `GET` | `/health` | |

## Cache

Question sets are cached in Postgres on
`(prompt version, subject, topic, difficulty mix, content hash of the retrieved
chunks, provider, model)`. The key deliberately excludes the user id, so two
students in the same band on the same topic share a generation. Re-ingesting
changed chapter text invalidates entries automatically via the content hash.

`GET /cache/stats` reports **measured** lookups, hits and hit rate. Put a
number on a resume only after measuring it against a real usage pattern — a
hit rate from a handful of manual clicks is not a benchmark.

## Deployment

- Backend + Postgres → Render or Railway (set `DATABASE_URL`, `JWT_SECRET`,
  `GEMINI_API_KEY`, `LLM_PROVIDER=gemini`, `CORS_ORIGINS`). Run
  `python -m scripts.ingest` once after the first deploy.
- Frontend → Vercel (set `NEXT_PUBLIC_API_URL` to the deployed API origin).

## Scope decisions

Cut deliberately, each with a reason — see [DESIGN.md](DESIGN.md) for the full
argument: Airflow, Git-LFS, full Grades 1–12 coverage, Redis/Elasticsearch/
Kafka/microservices, a reranking model, Alembic, and any unmeasured
cost-reduction claim.
