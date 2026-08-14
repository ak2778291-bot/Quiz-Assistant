"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, getUser, type ProficiencyList } from "@/lib/api";
import { DifficultyBadge, MixSummary, ScoreBar } from "@/components/Badge";

export default function ProficiencyPage() {
  const router = useRouter();
  const [data, setData] = useState<ProficiencyList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const user = getUser();
    if (!user) {
      router.push("/login");
      return;
    }
    api
      .proficiency(user.id)
      .then(setData)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Could not load proficiency"),
      );
  }, [router]);

  if (error) return <main className="notice error">{error}</main>;
  if (!data) return <main className="muted">Loading…</main>;

  return (
    <main>
      <h1>Your proficiency</h1>
      <p className="sub">
        One score per topic, updated after every answer, driving the difficulty
        of your next quiz on that topic.
      </p>

      <div className="notice info" style={{ marginBottom: 24 }}>
        <strong>The formula</strong>
        <p className="small mono" style={{ margin: "8px 0 0" }}>
          {data.formula}
        </p>
      </div>

      {data.topics.length === 0 ? (
        <div className="card">
          <p style={{ margin: 0 }}>
            No topics attempted yet. <Link href="/dashboard">Pick a chapter</Link>{" "}
            to get started — every new topic starts at{" "}
            <span className="mono">{data.initial_score.toFixed(2)}</span>.
          </p>
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Topic</th>
              <th>Answers</th>
              <th>Score</th>
              <th>Band</th>
              <th>Next quiz mix</th>
            </tr>
          </thead>
          <tbody>
            {data.topics.map((topic) => (
              <tr key={`${topic.subject_id}:${topic.topic}`}>
                <td>
                  <div>{topic.chapter}</div>
                  <div className="small muted">
                    {topic.subject} · Grade {topic.grade}
                  </div>
                </td>
                <td className="mono">{topic.answers_count}</td>
                <td>
                  <div className="mono">{topic.score.toFixed(4)}</div>
                  <ScoreBar score={topic.score} />
                </td>
                <td>
                  <DifficultyBadge difficulty={topic.band} />
                </td>
                <td>
                  <MixSummary mix={topic.next_quiz_mix} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
