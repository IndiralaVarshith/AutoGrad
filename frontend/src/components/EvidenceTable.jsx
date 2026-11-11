import React from 'react';
import { CheckCircle2, XCircle } from 'lucide-react';
import { Pill } from './ui';

export default function EvidenceTable({ criteria = [], onOpenRegion }) {
  if (!criteria || criteria.length === 0) {
    return <p className="py-1 text-sm text-muted">No marking points recorded for this question.</p>;
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-line">
      <table className="w-full min-w-[40rem] border-collapse text-left text-[13px]">
        <thead>
          <tr className="border-b border-line bg-sunken text-muted">
            <th scope="col" className="w-1/4 px-3 py-2 font-semibold">Marking point</th>
            <th scope="col" className="w-2/5 px-3 py-2 font-semibold">Quote from the answer</th>
            <th scope="col" className="px-3 py-2 font-semibold">On the page</th>
            <th scope="col" className="px-3 py-2 font-semibold">Quote check</th>
            <th scope="col" className="px-3 py-2 text-right font-semibold">Marks</th>
            <th scope="col" className="px-3 py-2 font-semibold">Notes</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {criteria.map((item, idx) => {
            const hasQuote = Boolean(item.verbatim_quote && item.verbatim_quote.trim());
            const takenOff = (item.marks_awarded ?? 0) < 0;
            return (
              <tr key={idx} className={`align-top transition-colors hover:bg-sunken/60${takenOff ? ' bg-bad-soft/40' : ''}`}>
                <td className="px-3 py-2.5">{item.criterion || `Point ${idx + 1}`}</td>
                <td className="px-3 py-2.5">
                  {hasQuote
                    ? <blockquote className="border-l-2 border-line-strong pl-2 text-ink">{item.verbatim_quote}</blockquote>
                    : <span className="text-muted">No quote found</span>}
                </td>
                <td className="px-3 py-2.5">
                  {item.evidence_region_ids?.length ? (
                    <div className="flex flex-wrap gap-1">
                      {item.evidence_region_ids.map((regionId) => (
                        <button
                          key={regionId}
                          type="button"
                          onClick={() => onOpenRegion?.(regionId)}
                          className="rounded-md bg-info-soft px-1.5 py-0.5 text-xs font-medium text-info"
                          title="Open the matching handwriting"
                        >
                          {regionId}
                        </button>
                      ))}
                    </div>
                  ) : <span className="text-muted">No clear match</span>}
                </td>
                <td className="px-3 py-2.5">
                  {item.quote_verified
                    ? <Pill tone="good" icon={CheckCircle2}>Verified</Pill>
                    : hasQuote
                      ? <Pill tone="bad" icon={XCircle}>Not verified</Pill>
                      : <span className="text-muted">None</span>}
                </td>
                <td className={`px-3 py-2.5 text-right font-semibold tabular-nums${takenOff ? ' text-bad' : ''}`}>{item.marks_awarded ?? 0}</td>
                <td className="px-3 py-2.5 text-muted">{item.rationale || 'None'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
