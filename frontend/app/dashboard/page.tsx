"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  getUser,
  type ProficiencyTopic,
  type Subject,
} from "@/lib/api";
import { DifficultyBadge, MixSummary } from "@/components/Badge";

export default function DashboardPage() {
  const router = useRouter();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [proficiency, setProficiency] = useState<Record<string, ProficiencyTopic>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const user = getUser();
      const [subjectList, profList] = await Promise.all([
        api.subjects(),
        user ? api.proficiency(user.id) : Promise.resolve(null),
      ]);
      setSubjects(subjectList);
      if (profList) {
        setProficiency(
          Object.fromEntries(
            profList.topics.map((t) => [`${t.subject_id}:${t.topic}`, t]),
          ),
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load subjects");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function startQuiz(subjectId: number, topic: string) {
    if (!getUser()) {
      router.push("/login");
      return;
    }
    setStarting(`${subjectId}:${topic}`);
    setError(null);
    try {
      const quiz = await api.createQuiz(subjectId, topic);
      router.push(`/quiz/${quiz.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the quiz");
      setStarting(null);
    }
  }

  if (loading) return <main className="muted">Loading subjects…</main>;

  return (
    <main>
      <h1>Choose a chapter</h1>
      <p className="sub">
        Only chapters with ingested curriculum content are listed — the
        catalogue is built from what is actually in the vector store, so a
        chapter shown here can always be grounded.
      </p>

      {error && (
        <div className="notice error" style={{ marginBottom: 20 }}>
          {error}
        </div>
      )}

      {subjects.length === 0 && (
        <div className="notice info">
          No content has been ingested yet. Run{" "}
          <code className="mono">python -m scripts.ingest</code> in the backend.
        </div>
      )}

      {subjects.map((subject) => (
        <section key={subject.id}>
          <h2>
            {subject.name} · Grade {subject.grade}
          </h2>
          <div className="grid">
            {subject.topics.map((topic) => {
              const key = `${subject.id}:${topic.topic}`;
              const prof = proficiency[key];
              return (
                <div className="card stack" key={topic.topic}>
                  <div>
                    <h3>{topic.chapter}</h3>
                    <p className="small muted" style={{ margin: 0 }}>
                      {topic.chunk_count} indexed passages
                    </p>
                  </div>

                  {prof ? (
                    <div className="small">
                      <div className="between">
                        <span className="muted">Proficiency</span>
                        <span className="mono">{prof.score.toFixed(4)}</span>
                      </div>
                      <div className="between" style={{ marginTop: 4 }}>
                        <span className="muted">Next quiz</span>
                        <span>
                          <DifficultyBadge difficulty={prof.band} />{" "}
                          <MixSummary mix={prof.next_quiz_mix} />
                        </span>
                      </div>
                    </div>
                  ) : (
                    <p className="small muted" style={{ margin: 0 }}>
                      Not attempted — starts at 0.50 (medium).
                    </p>
                  )}

                  <button
                    className="primary"
                    disabled={starting !== null}
                    onClick={() => startQuiz(subject.id, topic.topic)}
                  >
                    {starting === key ? "Generating…" : "Start quiz"}
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </main>
  );
}
