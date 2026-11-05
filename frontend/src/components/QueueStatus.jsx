import React from 'react';

const WAITING = new Set(['PENDING', 'PROCESSING']);
const STATUS_TEXT = {
  PENDING: 'Waiting',
  PROCESSING: 'Grading',
  COMPLETE: 'Done',
  NEEDS_REVIEW: 'Needs teacher review',
  FAILED: 'Failed, no marks given',
  TIMED_OUT: 'Timed out, no marks given',
  CANCELLED: 'Cancelled',
};

export default function QueueStatus({ queue, onResume }) {
  const jobs = queue.jobs || [];
  const total = queue.total_jobs || 0;
  const done = total - jobs.filter((job) => WAITING.has(job.status)).length;
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  const current = jobs.find((job) => job.status === 'PROCESSING');

  return (
    <div className="bg-[#121316] border border-[#22242a] rounded p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-xs font-mono uppercase tracking-wider text-[#8c9099] font-medium">
          Grading queue: {done} of {total} finished
        </div>
        {current && (
          <div className="font-mono text-xs text-[#a0a4ad]">Now: {current.hall_ticket}</div>
        )}
      </div>

      <div className="h-1.5 rounded bg-[#22242a] overflow-hidden">
        <div className="h-full bg-[#2563eb] transition-all" style={{ width: `${percent}%` }} />
      </div>

      {queue.paused && (
        <div className="flex items-center justify-between gap-3 border border-[#f59e0b]/40 rounded p-2 text-xs text-[#f59e0b]">
          <span>Paused: {queue.reason}</span>
          {queue.needs_resume && (
            <button
              type="button"
              onClick={onResume}
              className="shrink-0 bg-[#2563eb] text-white rounded px-3 py-1 cursor-pointer"
            >
              Resume
            </button>
          )}
        </div>
      )}

      <ul className="max-h-48 overflow-y-auto divide-y divide-[#22242a] text-xs font-mono">
        {jobs.map((job) => (
          <li key={job.job_id} className="flex justify-between gap-3 py-1">
            <span className="text-[#c8ccd4] truncate">{job.hall_ticket}</span>
            <span className="text-[#8c9099] text-right">
              {STATUS_TEXT[job.status] || job.status}
              {job.status === 'PROCESSING' && job.stage_detail ? `: ${job.stage_detail}` : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
