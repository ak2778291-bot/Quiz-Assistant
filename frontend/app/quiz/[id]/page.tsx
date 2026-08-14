"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type AnswerResponse, type Quiz } from "@/lib/api";
import { DifficultyBadge, MixSummary } from "@/components/Badge";

/**
 * The quiz is re-fetched by id (not regenerated) so a refresh mid-quiz
 * resumes — regenerating would burn an LLM call and silently change the
 * questions the proficiency score was computed against. GET /quizzes/{id}
 * never includes the answer key.
 */
export default function QuizPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const quizId = Number(params.id);

  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<AnswerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const loaded = await api.quiz(quizId);
      setQuiz(loaded);
      // Resume at the first unanswered question.
      const answered = new Set(loaded.answered_question_ids);
      const firstUnanswered = loaded.questions.findIndex(
        (q) => !answered.has(q.id),
      );
      setIndex(
        firstUnanswered === -1 ? loaded.questions.length - 1 : firstUnanswered,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the quiz");
    }
  }, [quizId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) return <main className="notice error">{error}</main>;
  if (!quiz) return <main className="muted">Loading quiz…</main>;

  const question = quiz.questions[index];
  const isLast = index === quiz.questions.length - 1;

  async function submit() {
    if (!selected || !question) return;
    setBusy(true);
    try {
      setFeedback(await api.answer(quizId, question.id, selected));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not submit the answer");
    } finally {
      setBusy(false);
    }
  }

  function next() {
    if (isLast) {
      router.push(`/quiz/${quizId}/results`);
      return;
    }
    setIndex(index + 1);
    setSelected(null);
    setFeedback(null);
  }

  return (
    <main>
      <div className="between" style={{ marginBottom: 6 }}>
        <h1 style={{ margin: 0, fontSize: 20 }}>{quiz.chapter}</h1>
        <span className="small muted">
          {quiz.subject} · Grade {quiz.grade}
        </span>
      </div>
      <p className="sub small">
        Quiz difficulty <DifficultyBadge difficulty={quiz.difficulty} /> chosen
        from your proficiency of{" "}
        <span className="mono">{quiz.adaptive.proficiency_score.toFixed(4)}</span>{" "}
        · mix <MixSummary mix={quiz.adaptive.mix} />
      </p>

      <div className="progress" style={{ marginBottom: 20 }}>
        <div style={{ width: `${((index + 1) / quiz.questions.length) * 100}%` }} />
      </div>

      <div className="card">
        <div className="between" style={{ marginBottom: 12 }}>
          <span className="small muted">
            Question {index + 1} of {quiz.questions.length}
          </span>
          <DifficultyBadge difficulty={question.difficulty} />
        </div>

        <p style={{ fontSize: 16, marginTop: 0 }}>{question.question_text}</p>

        {(["A", "B", "C", "D"] as const).map((letter) => {
          const classes = ["option"];
          if (feedback) {
            if (letter === feedback.correct_answer) classes.push("correct");
            else if (letter === feedback.selected_answer) classes.push("wrong");
          } else if (selected === letter) {
            classes.push("selected");
          }
          return (
            <button
              key={letter}
              className={classes.join(" ")}
              disabled={feedback !== null}
              onClick={() => setSelected(letter)}
            >
              <span className="letter">{letter}</span>
              <span>{question.options[letter]}</span>
            </button>
          );
        })}

        {feedback ? (
          <div className="stack" style={{ marginTop: 16 }}>
            <div className={`notice ${feedback.is_correct ? "info" : "error"}`}>
              <strong>
                {feedback.is_correct ? "Correct." : "Not quite."}
              </strong>{" "}
              {feedback.explanation}
              <div className="source">
                Grounded in source passage
                {feedback.source_chunk_ids.length > 1 ? "s" : ""}{" "}
                <span className="mono">
                  #{feedback.source_chunk_ids.join(", #")}
                </span>
              </div>
            </div>
            <div className="between">
              <span className="small muted">
                Proficiency now{" "}
                <span className="mono">
                  {feedback.proficiency_score.toFixed(4)}
                </span>{" "}
                <DifficultyBadge difficulty={feedback.proficiency_band} />
              </span>
              <button className="primary" onClick={next}>
                {isLast ? "See results" : "Next question"}
              </button>
            </div>
          </div>
        ) : (
          <button
            className="primary"
            style={{ marginTop: 12 }}
            disabled={!selected || busy}
            onClick={submit}
          >
            {busy ? "Checking…" : "Submit answer"}
          </button>
        )}
      </div>
    </main>
  );
}
