export function DifficultyBadge({ difficulty }: { difficulty: string }) {
  return <span className={`badge ${difficulty}`}>{difficulty}</span>;
}

export function MixSummary({ mix }: { mix: Record<string, number> }) {
  const parts = (["easy", "medium", "hard"] as const)
    .filter((d) => (mix[d] ?? 0) > 0)
    .map((d) => `${mix[d]} ${d}`);
  return <span className="mono">{parts.join(" · ")}</span>;
}

export function ScoreBar({ score }: { score: number }) {
  return (
    <div className="progress" style={{ maxWidth: 220 }}>
      <div style={{ width: `${Math.round(score * 100)}%` }} />
    </div>
  );
}
