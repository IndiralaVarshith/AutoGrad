import React from 'react';
import { ArrowRight, CheckCircle2, CircleDashed, Clock, Flag, Loader2, XCircle } from 'lucide-react';
import { Button, Callout, Pill, ProgressRing } from './ui';

const WAITING = new Set(['PENDING', 'PROCESSING']);
const STATUS = {
  PENDING: { text: 'Waiting', tone: 'neutral', icon: Clock },
  PROCESSING: { text: 'Grading', tone: 'info', icon: Loader2 },
  COMPLETE: { text: 'Done', tone: 'good', icon: CheckCircle2 },
  NEEDS_REVIEW: { text: 'Marked, some answers to check', tone: 'warn', icon: Flag },
  FAILED: { text: 'Failed, no marks given', tone: 'bad', icon: XCircle },
  TIMED_OUT: { text: 'Timed out, no marks given', tone: 'bad', icon: XCircle },
  CANCELLED: { text: 'Cancelled', tone: 'neutral', icon: CircleDashed },
};

export default function QueueStatus({ queue, onResume, onGoToReview }) {
  const jobs = queue.jobs || [];
  const total = queue.total_jobs || 0;
  const done = total - jobs.filter((job) => WAITING.has(job.status)).length;
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  const current = jobs.find((job) => job.status === 'PROCESSING');
  const finished = total > 0 && done === total;

  return (
    <section className="card rise space-y-4 p-5" aria-label="Grading queue">
      <div className="flex flex-wrap items-center gap-4">
        <ProgressRing value={percent} label={`${done} of ${total} sheets finished`}>{percent}%</ProgressRing>
        <div className="min-w-0 flex-1">
          <h2 className="text-lg">{finished ? 'All sheets are graded' : 'Grading your sheets'}</h2>
          <p className="truncate text-sm text-muted">
            {done} of {total} finished
            {current ? ` · now reading ${current.hall_ticket}` : ''}
          </p>
        </div>
        {finished && onGoToReview && (
          <Button variant="primary" onClick={onGoToReview}>
            Go to Review
            <ArrowRight size={16} aria-hidden="true" />
          </Button>
        )}
      </div>

      {queue.paused && (
        <Callout
          tone="warn"
          title="The queue is paused"
          action={queue.needs_resume ? <Button variant="primary" size="sm" onClick={onResume}>Resume</Button> : undefined}
        >
          {queue.reason}
        </Callout>
      )}

      <ul className="max-h-64 divide-y divide-line overflow-y-auto rounded-xl border border-line">
        {jobs.map((job) => {
          const status = STATUS[job.status] || { text: job.status, tone: 'neutral', icon: CircleDashed };
          return (
            <li key={job.job_id} className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm">
              <span className="min-w-0">
                <span className="block truncate font-medium">{job.hall_ticket}</span>
                {job.status === 'PROCESSING' && job.stage_detail && (
                  <span className="block truncate text-[13px] text-muted">{job.stage_detail}</span>
                )}
              </span>
              <Pill tone={status.tone} icon={status.icon} className={job.status === 'PROCESSING' ? '[&>svg]:animate-spin' : undefined}>
                {status.text}
              </Pill>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
