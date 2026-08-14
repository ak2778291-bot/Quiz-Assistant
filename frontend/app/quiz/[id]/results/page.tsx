"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type QuizResult } from "@/lib/api";
import { DifficultyBadge, MixSummary, ScoreBar } from "@/components/Badge";

export default function ResultsPage() {
  const params = useParams<{ id: string }>();
  const quizId = Number(params.id);
  const [result, setResult] = useState<QuizResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .results(quizId)
      .then(setResult)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Could not load results"),
      );
  }, [quizId]);

  if (error) return <main className="notice error">{error}</main>;
  if (!result) return <main className="muted">Loading results…</main>;

  const delta = result.score_after - result.score_before;
  const sourceById = new Map(result.sources.map((s) => [s.id, s]));

  return (
    <main>
      <h1>{result.chapter}</h1>
      <p className="sub">
        {result.subject} · Grade {result.grade} — you scored{" "}
        <strong>
          {result.correct} / {result.total}
        </strong>
      </p>

      <div className="card stack" style={{ marginBottom: 24 }}>
        <h3 style={{ margin: 0 }}>How your proficiency changed</h3>
        <table>
          <tbody>
            <tr>
              <td className="muted">Before this quiz</td>
              <td className="mono">{result.score_before.toFixed(4)}</td>
              <td>
                <DifficultyBadge difficulty={result.band_before} />
              </td>
            </tr>
            <tr>
              <td className="muted">After this quiz</td>
              <td className="mono">
                {result.score_after.toFixed(4)}{" "}
                <span
                  style={{
                    color: delta >= 0 ? "var(--green)" : "var(--red)",
                  }}
                >
                  ({delta >= 0 ? "+" : ""}
                  {delta.toFixed(4)})
                </span>
              </td>
              <td>
                <DifficultyBadge difficulty={result.band_after} />
              </td>
            </tr>
            <tr>
              <td className="muted">Your next quiz</td>
              <td colSpan={2}>
                <MixSummary mix={result.next_quiz_mix} />
              </td>
            </tr>
          </tbody>
        </table>
        <ScoreBar score={result.score_after} />
      </div>

      <h2>Question review</h2>
      <div className="stack">
        {result.questions.map((question) => (
          <div className="card" key={question.id}>
            <div className="between" style={{ marginBottom: 10 }}>
              <span className="small muted">Question {question.position + 1}</span>
              <span className="row">
                <DifficultyBadge difficulty={question.difficulty} />
                {question.is_correct === null ? (
                  <span className="badge">not answered</span>
                ) : question.is_correct ? (
                  <span className="badge easy">correct</span>
                ) : (
                  <span className="badge hard">incorrect</span>
                )}
              </span>
            </div>

            <p style={{ marginTop: 0 }}>{question.question_text}</p>

            {(["A", "B", "C", "D"] as const).map((letter) => {
              const classes = ["option"];
              if (question.correct_answer === letter) classes.push("correct");
              else if (question.selected_answer === letter) classes.push("wrong");
              return (
                <div className={classes.join(" ")} key={letter}>
                  <span className="letter">{letter}</span>
                  <span>{question.options[letter]}</span>
                </div>
              );
            })}

            {question.explanation && (
              <p className="small muted" style={{ marginBottom: 0 }}>
                {question.explanation}
              </p>
            )}

            <div className="source">
              <strong>Source passage(s)</strong>
              {question.source_chunk_ids.map((id) => {
                const chunk = sourceById.get(id);
                return (
                  <p key={id} style={{ margin: "6px 0 0" }}>
                    <span className="mono">
                      #{id} · {chunk?.chapter} · passage {(chunk?.chunk_index ?? 0) + 1}
                    </span>
                    {chunk && <br />}
                    {chunk?.content}
                  </p>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="row" style={{ marginTop: 24 }}>
        <Link href="/dashboard" className="btn primary">
          Take another quiz
        </Link>
        <Link href="/proficiency" className="btn">
          View all proficiency scores
        </Link>
      </div>
    </main>
  );
}
