import React from 'react';

export default function EvidenceTable({ criteria = [], questionNumber }) {
  if (!criteria || criteria.length === 0) {
    return (
      <div className="text-xs text-[#6e737d] italic py-2">
        No evaluation criteria defined for this question.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto border border-[#22242a] bg-[#111215] rounded">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className="border-b border-[#22242a] bg-[#15171b] text-[#8c9099] font-mono text-[11px] uppercase tracking-wider">
            <th className="py-2.5 px-3 w-1/4 font-medium">Criterion</th>
            <th className="py-2.5 px-3 w-2/5 font-medium">Student Answer Quote</th>
            <th className="py-2.5 px-3 text-center w-28 font-medium">Verification</th>
            <th className="py-2.5 px-3 text-right w-20 font-medium">Score</th>
            <th className="py-2.5 px-3 font-medium">Grader Notes</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#1c1e24]">
          {criteria.map((item, idx) => {
            const isVerified = item.quote_verified;
            const hasQuote = Boolean(item.verbatim_quote && item.verbatim_quote.trim());

            return (
              <tr key={idx} className="hover:bg-[#16171b] transition-colors">
                {/* Criterion */}
                <td className="py-2.5 px-3 text-[#d1d4db] font-normal align-top leading-relaxed">
                  {item.criterion || `Criterion #${idx + 1}`}
                </td>

                {/* Verbatim Quote */}
                <td className="py-2.5 px-3 align-top font-mono text-[11px] leading-relaxed">
                  {hasQuote ? (
                    <div className="text-[#c4c7cf] bg-[#16181d] border border-[#242730] rounded px-2 py-1">
                      "{item.verbatim_quote}"
                    </div>
                  ) : (
                    <span className="text-[#595d66] italic">No direct citation found</span>
                  )}
                </td>

                {/* Verification Status */}
                <td className="py-2.5 px-3 text-center align-top font-mono text-[10px]">
                  {isVerified ? (
                    <span className="inline-block px-1.5 py-0.5 rounded bg-[#112217] text-[#4ade80] border border-[#1b3825]">
                      VERIFIED
                    </span>
                  ) : hasQuote ? (
                    <span className="inline-block px-1.5 py-0.5 rounded bg-[#261212] text-[#ef4444] border border-[#441d1d]">
                      UNVERIFIED
                    </span>
                  ) : (
                    <span className="text-[#595d66]">—</span>
                  )}
                </td>

                {/* Marks Awarded */}
                <td className="py-2.5 px-3 text-right font-mono font-medium text-[#e1e4ea] align-top">
                  <span className={item.marks_awarded > 0 ? 'text-[#e1e4ea]' : 'text-[#6e737d]'}>
                    {item.marks_awarded ?? 0}
                  </span>
                </td>

                {/* Rationale */}
                <td className="py-2.5 px-3 text-[#7e828d] align-top leading-relaxed text-[11px]">
                  {item.rationale || '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
