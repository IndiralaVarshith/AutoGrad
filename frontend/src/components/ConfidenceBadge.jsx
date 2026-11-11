import React from 'react';
import { AlertTriangle, CheckCircle2, CircleDashed, Flag } from 'lucide-react';
import { Pill } from './ui';

export default function ConfidenceBadge({ routing, score }) {
  const normRouting = (routing || '').toUpperCase();
  const numScore = typeof score === 'number' ? score : null;

  let tone = 'neutral';
  let icon = CircleDashed;
  let label = normRouting ? normRouting.replaceAll('_', ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase()) : 'Pending';

  // The decision comes first. A high confidence never makes a flagged answer look accepted.
  if (normRouting === 'AUTO_ACCEPT' || (!normRouting && numScore !== null && numScore >= 0.85)) {
    tone = 'good';
    icon = CheckCircle2;
    label = 'Accepted';
  } else if (normRouting === 'FLAGGED' || (!normRouting && numScore !== null && numScore >= 0.65)) {
    tone = 'warn';
    icon = Flag;
    label = 'Flagged';
  } else if (normRouting === 'MANUAL' || (!normRouting && numScore !== null && numScore < 0.65)) {
    tone = 'bad';
    icon = AlertTriangle;
    label = 'Review required';
  }

  return (
    <Pill tone={tone} icon={icon}>
      {label}
      {numScore !== null && <span className="font-medium opacity-75 tabular-nums">{(numScore * 100).toFixed(0)}%</span>}
    </Pill>
  );
}
