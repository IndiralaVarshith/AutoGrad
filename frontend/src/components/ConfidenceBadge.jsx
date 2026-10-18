import React from 'react';

export default function ConfidenceBadge({ routing, score, size = 'md' }) {
  const normRouting = (routing || '').toUpperCase();
  const numScore = typeof score === 'number' ? score : null;

  let style = 'bg-[#181a1d] text-[#8c9099] border-[#25282e]';
  let label = normRouting || 'PENDING';

  if (normRouting === 'AUTO_ACCEPT' || (numScore !== null && numScore >= 0.85)) {
    style = 'bg-[#112217] text-[#4ade80] border-[#1b3825]';
    label = 'Accepted';
  } else if (normRouting === 'FLAGGED' || (numScore !== null && numScore >= 0.65)) {
    style = 'bg-[#241a10] text-[#f59e0b] border-[#3c2a17]';
    label = 'Flagged';
  } else if (normRouting === 'MANUAL' || (numScore !== null && numScore < 0.65)) {
    style = 'bg-[#261212] text-[#ef4444] border-[#441d1d]';
    label = 'Review Required';
  }

  const scoreText = numScore !== null ? ` ${(numScore * 100).toFixed(0)}%` : '';

  return (
    <span
      className={`inline-flex items-center gap-1 font-mono uppercase tracking-wider border rounded px-2 py-0.5 font-medium ${
        size === 'sm' ? 'text-[10px]' : 'text-[11px]'
      } ${style}`}
    >
      <span>{label}</span>
      {scoreText && <span className="opacity-70">{scoreText}</span>}
    </span>
  );
}
