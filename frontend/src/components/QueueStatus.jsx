import React from 'react';

export default function QueueStatus({ queue = [], stats = null }) {
  const total = stats?.total ?? queue.length;
  const processing = stats?.processing ?? queue.filter((i) => i.status === 'PROCESSING').length;
  const completed = stats?.completed ?? queue.filter((i) => i.status === 'COMPLETE').length;
  const needsReview = stats?.needsReview ?? queue.filter((i) => i.status === 'NEEDS_REVIEW' || i.flagged_count > 0).length;

  const progressPercent = total > 0 ? Math.round(((completed + needsReview) / total) * 100) : 0;

  return (
    <div className="bg-[#121316] border border-[#22242a] rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs font-mono uppercase tracking-wider text-[#8c9099] font-medium">
          Grading Queue Progress
        </div>
        {processing > 0 && (
          <div className="font-mono text-xs text-[#a0a4ad]">
            Processing {processing} {processing === 1 ? 'sheet' : 'sheets'}...
          </div>
        )}
      </div>

      {/* Progress Bar (Flat, solid) */}
      <div className="w-full bg-[#1c1e23] h-1.5 rounded overflow-hidden mb-4">
        <div
          className="bg-[#3b82f6] h-full transition-all duration-300"
          style={{ width: `${progressPercent}%` }}
        />
      </div>

      {/* Metric Cards (Flat, dark) */}
      <div className="grid grid-cols-4 gap-2 text-center font-mono">
        <div className="bg-[#16181c] p-2.5 rounded border border-[#22252c]">
          <div className="text-[11px] text-[#717680] uppercase">Total</div>
          <div className="text-base font-semibold text-[#e1e4ea] mt-0.5">{total}</div>
        </div>

        <div className="bg-[#16181c] p-2.5 rounded border border-[#22252c]">
          <div className="text-[11px] text-[#717680] uppercase">Processing</div>
          <div className="text-base font-semibold text-[#a5b4fc] mt-0.5">{processing}</div>
        </div>

        <div className="bg-[#16181c] p-2.5 rounded border border-[#22252c]">
          <div className="text-[11px] text-[#717680] uppercase">Accepted</div>
          <div className="text-base font-semibold text-[#4ade80] mt-0.5">{completed}</div>
        </div>

        <div className="bg-[#16181c] p-2.5 rounded border border-[#22252c]">
          <div className="text-[11px] text-[#717680] uppercase">Flagged</div>
          <div className="text-base font-semibold text-[#f59e0b] mt-0.5">{needsReview}</div>
        </div>
      </div>
    </div>
  );
}
