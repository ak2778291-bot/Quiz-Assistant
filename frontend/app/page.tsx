import Link from "next/link";

export default function Home() {
  return (
    <main>
      <h1>Quizzes generated from the textbook, not from the model.</h1>
      <p className="sub">
        Pick a chapter. Every question is generated only from NCERT content
        retrieved for that chapter, and cites the passage it came from. Your
        per-topic proficiency score updates after every answer and
        deterministically sets the difficulty of your next quiz.
      </p>

      <div className="grid">
        <div className="card">
          <h3>Curriculum-grounded</h3>
          <p className="small muted">
            Retrieval is scoped to the chosen chapter in SQL before ranking, and
            every generated question is re-checked against the chunks that were
            actually retrieved. A question that cites anything else is rejected,
            not served.
          </p>
        </div>
        <div className="card">
          <h3>Deterministic adaptation</h3>
          <p className="small muted">
            Proficiency is an exponentially-weighted moving average in [0,1]
            starting at 0.50, moving 30% toward a difficulty-weighted target
            after each answer. The next quiz&apos;s difficulty mix is a pure
            function of that score.
          </p>
        </div>
        <div className="card">
          <h3>Traceable</h3>
          <p className="small muted">
            The results page shows the exact source passages behind each
            question, so you can check any answer against the chapter text.
          </p>
        </div>
      </div>

      <div className="row" style={{ marginTop: 28 }}>
        <Link href="/register" className="btn primary">
          Create an account
        </Link>
        <Link href="/dashboard" className="btn">
          Browse subjects
        </Link>
      </div>
    </main>
  );
}
