# DESIGN.md — decisions, and why

This is the interview cheat sheet. Every choice below is one I should be able
to defend in one or two sentences without apologising for it.

---

## 1. What this project is for

A quiz platform with exactly two ideas worth building for real:

1. Questions are generated **only** from retrieved NCERT content for the
   selected topic, and every question is traceable to the chunks it came from.
2. A per-student, per-topic proficiency score, updated after every answer,
   **deterministically** sets the difficulty distribution of the next quiz.

Everything else exists to make those two demonstrable. Anything that made the
platform more *complete* without making those two more *convincing* was cut.

It also reuses the retrieval architecture from my Knowledge Assistant project
on purpose. The story is "I applied a retrieval pattern I'd already built to a
new domain and added stateful, personalised selection logic on top" — not two
disconnected RAG demos.

---

## 2. The proficiency formula (spec 4.2)

**One sentence:** per-topic proficiency is an exponentially-weighted moving
average in `[0, 1]` that starts at `0.50` and, after every answer, moves 30% of
the way toward a target set by that question's difficulty and correctness.

```
score ← round(score + α × (target − score), 4)          α = 0.30
```

| difficulty | target if correct | target if incorrect |
|---|---|---|
| easy | 0.60 | 0.00 |
| medium | 0.80 | 0.20 |
| hard | 1.00 | 0.40 |

### Why difficulty-weighted targets

A flat `target = 1.0 if correct else 0.0` treats "got an easy recall question
right" as identical evidence to "reasoned through a hard one". The table fixes
that in both directions:

- A correct **easy** answer targets 0.60, so a run of easy wins asymptotes at
  0.60 and can never on its own push a student into the hard band (≥ 0.70).
  Mastery has to be demonstrated on harder questions.
- An incorrect **easy** answer targets 0.00 — strong negative evidence.
- An incorrect **hard** answer targets 0.40 — mild. Missing a hard question is
  expected and shouldn't crater the score.

### Why an EWMA rather than a running average or a streak counter

- **Recency for free.** The most recent answer always contributes exactly 30%
  of the new score. A student who has improved isn't held back by answers from
  three weeks ago the way a running average would hold them.
- **O(1) state.** One `NUMERIC` column per (user, topic). No answer history to
  scan, no window to maintain.
- **Bounded and monotone.** The score stays in `[0, 1]` by construction, and a
  correct answer never lowers it. That makes the behaviour explainable to a
  student, which a tuned multi-factor model would not be.

`α = 0.30` is the one genuinely tuned constant: at 0.30 it takes roughly three
consistent answers to move a band, which felt right for 5-question quizzes.
Lower α is more stable but slower to adapt; higher α makes a single unlucky
answer swing the next quiz. It is a single named constant, so it is one line to
change.

### Why half-up rounding

Python's built-in `round` is banker's rounding, so the exact expected values in
the scripted-sequence test would depend on floating-point parity. `_round`
quantises with `ROUND_HALF_UP` via `Decimal`, which makes the stored score a
clean 4-decimal number and the test's expected values hand-derivable. Storage
is `NUMERIC(6,4)`, not float, for the same reason.

### Bands and the difficulty mix

```
score < 0.40 → easy      score < 0.70 → medium      else → hard
```

The band selects an integer weight vector, and the quiz's `n` questions are
allocated across it by the **largest-remainder method**, with ties broken in
the fixed order easy → medium → hard:

| band | easy | medium | hard |
|---|---|---|---|
| easy | 3 | 2 | 0 |
| medium | 1 | 3 | 1 |
| hard | 0 | 2 | 3 |

Two properties this gives, both tested:

- the allocation always sums to exactly `n`, for any `n`;
- `hard` count is monotone non-decreasing in score — a stronger student never
  receives an easier mix.

A mix rather than a single difficulty level matters: a quiz that is uniformly
"hard" gives no signal about what the student *has* mastered, and the easy
questions in a hard quiz are what stop the score from free-falling on a bad day.

### Tracing it live

`replay([(difficulty, correct), ...])` reruns any answer sequence and returns
the exact state, and `explain(score, n)` returns the band, the rule and the
mix. `GET /users/{id}/proficiency` returns the formula string itself, so the
UI displays the rule it is actually running.

---

## 3. Grounding (spec 4.1)

Three layers, in order. Only the third is a guarantee.

**Layer 1 — retrieval scoping.** The topic filter is applied in SQL *before*
vector ranking:

```sql
WHERE subject_id = :subject_id AND topic = :topic
ORDER BY embedding <=> :query_vector
LIMIT :k
```

This is the important design choice in the retrieval layer. A post-filter would
make grounding depend on the embeddings being good enough to keep chapters
apart. A pre-filter makes it structurally impossible for another chapter's text
to reach the prompt, so the guarantee holds even with a mediocre embedder.

**Layer 2 — the prompt.** Excerpts are numbered with their real database ids;
the model is told the excerpts are its only permitted source, told to produce
*fewer* questions rather than invent content, and required to cite the ids it
used. Best-effort — a prompt is not an enforcement mechanism.

**Layer 3 — validation.** `validate_generated_questions` re-checks every cited
id against the set that was actually retrieved. Two different failure modes,
handled differently on purpose:

- **Ungrounded citation** → `UngroundedGenerationError`, the whole generation
  is refused. A citation to a chunk that was never retrieved is evidence the
  model answered from general knowledge, and a partially-ungrounded set must
  not be quietly trimmed and served.
- **Malformed structure** (missing options, bad answer letter, duplicate text)
  → that question is dropped with a recorded reason, the rest are kept. This is
  a formatting failure, not an integrity failure.

`generate_questions` also refuses to call the model at all when the chunk list
is empty, so no code path can prompt with an empty context.

**Traceability.** `questions.source_chunk_ids INT[]` stores the citation, and
the results endpoint returns the full text of those chunks so any answer can be
checked against the chapter.

### The negative path

`POST /quizzes` for a topic with fewer than `MIN_CHUNKS_FOR_GENERATION` (2)
indexed chunks returns **422** with a message that names the topic and says
the system is refusing rather than falling back. The check runs *before* the
LLM call, and no quiz or proficiency row is created. The catalogue endpoint is
built from `content_chunks`, so the UI can only ever offer groundable topics —
the 422 is a backstop for direct API calls, not the normal path.

---

## 4. Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI (async) | Same as Knowledge Assistant — Pydantic validation and async are skill reuse, not new learning cost inside a tight slot. |
| Database | PostgreSQL + pgvector | One system for relational (users, quizzes, proficiency) *and* vector (chunks) data. No second service to run, back up or keep consistent. |
| ORM | SQLAlchemy 2.0 async + asyncpg | Typed `Mapped[...]` models; `pgvector.sqlalchemy.Vector` gives cosine distance as a first-class column expression. |
| LLM | Gemini via REST (`httpx`) | REST rather than the SDK: one fewer dependency to version-pin, and JSON mode with a `responseSchema` is a direct HTTP feature. Also a second real provider integration alongside Knowledge Assistant. |
| Frontend | Next.js App Router, plain CSS | Next.js is a deliberate point of difference from my React projects. Plain CSS over Tailwind because the UI is six pages — a utility framework would be config for no benefit. |
| Auth | JWT (PyJWT) + bcrypt | Needed to track proficiency across sessions. Same pattern as my other projects. |
| Ingestion | One manually-triggered script | See below. |
| Tests | Pytest | Depth on the two non-negotiables over broad coverage. |

### Why `bcrypt` directly instead of `passlib`

`passlib`'s bcrypt backend breaks against bcrypt 4.x, and `passlib` is
effectively unmaintained. Calling `bcrypt.hashpw` / `checkpw` directly is four
lines and has no compatibility surface.

### Why no Alembic

The schema is small, there is no production data to migrate, and `create_all`
on startup keeps the SQLAlchemy models the single source of truth. Alembic is
the first thing to add the moment this has real users — the models are already
in the shape Alembic autogenerates from.

---

## 5. Embeddings: two providers, one dimension

`GeminiEmbedder` (`text-embedding-004`) is the real provider. `LocalEmbedder`
is a deterministic signed hashing-trick bag-of-words embedder — sublinear TF,
two hashes per token to halve collision damage, L2-normalised, small stoplist.

Both emit 768 dimensions, so the `Vector(768)` column doesn't change when you
switch providers. Re-run ingestion after switching, because the two vector
spaces are unrelated.

The local provider exists for three reasons: `docker compose up` works with no
API key; retrieval tests are reproducible without mocking the thing under test;
and ingestion of the whole corpus costs nothing during development. It is a
real lexical retriever, and `test_ingestion_and_retrieval.py` asserts it ranks
the correct chapter first for four different queries — if that ever fails, the
offline demo is retrieving noise.

**What it can't do:** no semantic similarity. A query using different words
than the chapter ("how plants make food" vs "photosynthesis") will miss.
That's the honest trade, and it's why `gemini` is the default in
`config.py`.

### The `stub` LLM provider

`LLM_PROVIDER=stub` builds cloze questions by blanking the most distinctive
term from a sentence in a retrieved chunk, with distractors drawn from other
retrieved chunks of the same topic. It is:

- **explicitly opt-in** — never selected as a fallback when a key is missing;
  a missing key returns 503 with a message saying so;
- **still grounded** — it can only cite chunks it was handed, and
  `test_grounding.py` runs the same validation against it.

It exists so the deployed demo and the integration test are cost-free and
deterministic. It is not pretending to be the model.

---

## 6. Caching

Postgres table, keyed on a SHA-256 of `(prompt version, subject_id, topic,
difficulty mix, content fingerprint of the retrieved chunks, provider, model)`.

- **User id is excluded** on purpose — two students in the same band on the
  same topic is exactly the repeat-request case worth saving.
- **Content fingerprint** is `chunk_id:source_hash` pairs, so re-ingesting
  changed chapter text invalidates entries automatically.
- **Prompt version** is bumped by hand when the prompt or validation contract
  changes, so a stale entry from an older prompt is never served.

`cache_stats` counts lookups and hits so `GET /cache/stats` reports a
**measured** hit rate. No number goes on a resume until it comes from a real
usage pattern — the original spec explicitly warns against unverifiable
cost-reduction claims, and a hit rate from a few manual clicks isn't a
benchmark.

Not Redis: there is no throughput problem at this size, and a second datastore
for a table this small is complexity without a reason.

---

## 7. Cuts, each with its reason

| Cut | Why |
|---|---|
| **Airflow** | Airflow earns its complexity with multiple sources, recurring schedules and cross-task retry orchestration. A one-time ingestion of a curated corpus has none of those — it's a script. Airflow is the right next step *if* this needed to ingest new NCERT content on a recurring schedule. |
| **Git-LFS** | Git-LFS solves large-binary version control across a team. There are no binary assets with history here; extracted text lives in Postgres. |
| **Full Grades 1–12** | A data-acquisition and licensing problem at scale, not an engineering one. It would add ingestion volume, not a new technical idea. The pipeline is fully general — 12 chapters exercise every code path that 1,200 would. |
| **Redis / Elasticsearch / Kafka / microservices** | No throughput, search-relevance-at-scale, or event-streaming problem exists at this size. |
| **Reranking model** | Real value on a large corpus; marginal on a curated one where the SQL pre-filter has already narrowed retrieval to a single chapter. |
| **Alembic** | See §4. |
| **Multi-environment CI/CD** | One lint + test + build workflow captures the signal. |
| **A stated "40% cost reduction"** | Only a number measured against my own cache hit rate goes on a resume. `/cache/stats` is where that number would come from. |

---

## 8. Schema notes

The starting schema in the spec, plus a few columns. Each addition earns its
place:

| Table | Added | Why |
|---|---|---|
| `content_chunks` | `topic` | URL-safe slug of `chapter`; the indexed join key for retrieval and for the proficiency row. `slugify` is shared by ingestion and the API so the keys always match. |
| `content_chunks` | `source_hash` | Lets ingestion skip unchanged files, and feeds the cache key. |
| `quizzes` | `topic` | Proficiency is per topic, so a quiz must record which one it targeted. |
| `quizzes` | `proficiency_at_creation` | Freezes the score that drove the difficulty, so the adaptive decision is auditable after the score has moved on. This is what makes `score_before` on the results page truthful. |
| `questions` | `difficulty` | A quiz is a *distribution* of difficulties, not one level — and the proficiency update needs the difficulty of the specific question answered. |
| `questions` | `position` | Stable ordering; the mix is presented easy → medium → hard. |
| `proficiency` | `answers_count` | Distinguishes "0.50 because it's new" from "0.50 after 20 answers". Surfaced in the UI. |
| `attempts` | unique `(question_id, user_id)` | Makes answering idempotent **at the database level**, not just in application logic — the proficiency update can't be double-counted by a double-click or a retry. |

---

## 9. Things I'd do next, in order

1. **Alembic**, the moment there's data worth not dropping.
2. **An IVFFlat/HNSW index** on `content_chunks.embedding`. Not yet: the corpus
   is 79 chunks and every query is pre-filtered to one chapter (5–8 rows), so a
   sequential scan beats an index probe and an ANN index would trade exactness
   for nothing.
3. **Airflow**, *if* recurring multi-source ingestion becomes real.
4. **Calibrate α against real answer data** rather than judgement. Right now
   0.30 is defensible, not measured.
5. **Question quality feedback** — a "this question doesn't follow from the
   passage" report, which is the only honest way to measure grounding quality
   beyond citation checking. Citation validation proves a question *cites* the
   right chunk; it can't prove the question is *answerable* from it.
