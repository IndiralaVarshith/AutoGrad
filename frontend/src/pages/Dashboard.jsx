import React, { useState, useEffect } from 'react';
import { collection, onSnapshot } from 'firebase/firestore';
import { db } from '../firebase';
import api from '../api';
import ConfidenceBadge from '../components/ConfidenceBadge';
import EvidenceTable from '../components/EvidenceTable';
import ExportButton from '../components/ExportButton';

export default function Dashboard({ examId = 'EXAM_2026_01', onGoToReview }) {
  const [currentExamId, setCurrentExamId] = useState(examId);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedStudent, setExpandedStudent] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterStatus, setFilterStatus] = useState('ALL');

  useEffect(() => {
    if (examId) setCurrentExamId(examId);
  }, [examId]);

  useEffect(() => {
    let unsubscribe = null;
    let pollInterval = null;

    const fetchViaApi = async () => {
      try {
        const data = await api.getExamResults(currentExamId);
        if (data && Array.isArray(data.results)) {
          setResults(data.results);
        }
      } catch (err) {
        console.warn('API fetch notice:', err.message);
      } finally {
        setLoading(false);
      }
    };

    try {
      const resultsRef = collection(db, 'exams', currentExamId, 'results');
      unsubscribe = onSnapshot(
        resultsRef,
        (snapshot) => {
          const list = [];
          snapshot.forEach((doc) => {
            list.push(doc.data());
          });
          if (list.length > 0) {
            setResults(list);
            setLoading(false);
          } else {
            fetchViaApi();
          }
        },
        () => {
          fetchViaApi();
          pollInterval = setInterval(fetchViaApi, 5000);
        }
      );
    } catch (e) {
      fetchViaApi();
      pollInterval = setInterval(fetchViaApi, 5000);
    }

    return () => {
      if (unsubscribe) unsubscribe();
      if (pollInterval) clearInterval(pollInterval);
    };
  }, [currentExamId]);

  const filteredResults = results.filter((r) => {
    const q = searchQuery.toLowerCase();
    const matchesSearch =
      (r.hall_ticket || '').toLowerCase().includes(q) ||
      (r.student_name || '').toLowerCase().includes(q);

    if (!matchesSearch) return false;

    if (filterStatus === 'FLAGGED') {
      return r.status === 'NEEDS_REVIEW' || (r.flagged_count && r.flagged_count > 0);
    }
    if (filterStatus === 'AUTO') {
      return r.status === 'COMPLETE' && (!r.flagged_count || r.flagged_count === 0);
    }
    return true;
  });

  const totalPapers = results.length;
  const flaggedCount = results.filter((r) => r.status === 'NEEDS_REVIEW' || r.flagged_count > 0).length;
  const autoCount = results.filter((r) => r.status === 'COMPLETE' && !r.flagged_count).length;
  const avgScore = totalPapers > 0
    ? (results.reduce((acc, r) => acc + (r.total_score || 0), 0) / totalPapers).toFixed(1)
    : '0';

  return (
    <div className="max-w-7xl mx-auto py-6 px-4 sm:px-6 space-y-5">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-[#22242a] pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-bold text-[#ededed] tracking-tight">
              Evaluation Results
            </h1>
            <span className="font-mono text-xs text-[#717680]">
              [{currentExamId}]
            </span>
          </div>
          <p className="text-xs text-[#717680] mt-0.5 font-mono">
            Tabular evaluation records and evidence verification status
          </p>
        </div>

        <div className="flex items-center gap-2">
          <ExportButton examId={currentExamId} disabled={results.length === 0} />
          <button
            onClick={() => setLoading(true)}
            className="px-2.5 py-1.5 rounded bg-[#16181d] hover:bg-[#202227] text-[#8c9099] border border-[#25282f] text-xs font-mono transition-colors cursor-pointer"
          >
            {loading ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* KPI Cards (Flat, dark) */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 font-mono">
        <div className="bg-[#121316] border border-[#22242a] rounded p-3.5">
          <div className="text-[10px] text-[#717680] uppercase tracking-wider">Total Evaluated</div>
          <div className="text-xl font-semibold text-[#ededed] mt-0.5">{totalPapers}</div>
        </div>

        <div className="bg-[#121316] border border-[#22242a] rounded p-3.5">
          <div className="text-[10px] text-[#717680] uppercase tracking-wider">Auto Accepted</div>
          <div className="text-xl font-semibold text-[#4ade80] mt-0.5">{autoCount}</div>
        </div>

        <div className="bg-[#121316] border border-[#22242a] rounded p-3.5">
          <div className="text-[10px] text-[#717680] uppercase tracking-wider">Flagged for Review</div>
          <div className="text-xl font-semibold text-[#f59e0b] mt-0.5">{flaggedCount}</div>
        </div>

        <div className="bg-[#121316] border border-[#22242a] rounded p-3.5">
          <div className="text-[10px] text-[#717680] uppercase tracking-wider">Class Mean Score</div>
          <div className="text-xl font-semibold text-[#ededed] mt-0.5">{avgScore}</div>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-[#121316] p-2.5 rounded border border-[#22242a]">
        <div className="w-full sm:w-72">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search hall ticket or student..."
            className="w-full bg-[#0c0d0e] border border-[#25282f] rounded px-3 py-1.5 text-xs text-[#ededed] placeholder-[#4d515a] focus:outline-none focus:border-[#404552] font-mono"
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto font-mono text-xs">
          <span className="text-[#717680]">Filter:</span>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="bg-[#0c0d0e] border border-[#25282f] rounded px-2 py-1 text-xs text-[#ededed] focus:outline-none"
          >
            <option value="ALL">All ({results.length})</option>
            <option value="FLAGGED">Flagged ({flaggedCount})</option>
            <option value="AUTO">Accepted ({autoCount})</option>
          </select>
        </div>
      </div>

      {/* Results Table */}
      <div className="bg-[#121316] border border-[#22242a] rounded overflow-hidden">
        {loading && results.length === 0 ? (
          <div className="p-10 text-center text-[#717680] font-mono text-xs">
            Loading evaluation records...
          </div>
        ) : filteredResults.length === 0 ? (
          <div className="p-10 text-center text-[#717680] space-y-1">
            <div className="text-xs font-mono text-[#b0b4be]">No records match query</div>
            <div className="text-[11px] text-[#555963]">Upload papers to view results.</div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-[#22242a] text-[#8c9099] uppercase tracking-wider font-mono text-[11px] bg-[#15171b]">
                  <th className="py-2.5 px-3 w-8"></th>
                  <th className="py-2.5 px-3 font-medium">Hall Ticket</th>
                  <th className="py-2.5 px-3 font-medium">Student Name</th>
                  <th className="py-2.5 px-3 text-center font-medium">Score</th>
                  <th className="py-2.5 px-3 text-center font-medium">Confidence</th>
                  <th className="py-2.5 px-3 text-center font-medium">Status</th>
                  <th className="py-2.5 px-3 text-right font-medium">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1a1c21]">
                {filteredResults.map((paper, idx) => {
                  const isExpanded = expandedStudent === paper.hall_ticket;
                  const hasFlagged = paper.flagged_count > 0 || paper.status === 'NEEDS_REVIEW';

                  return (
                    <React.Fragment key={paper.hall_ticket || idx}>
                      <tr 
                        className={`hover:bg-[#16181d] transition-colors cursor-pointer ${
                          isExpanded ? 'bg-[#15171b]' : ''
                        }`}
                        onClick={() => setExpandedStudent(isExpanded ? null : paper.hall_ticket)}
                      >
                        <td className="py-2.5 px-3 text-center text-[#555963] font-mono">
                          {isExpanded ? '▼' : '▶'}
                        </td>
                        <td className="py-2.5 px-3 font-mono font-medium text-[#ededed]">
                          {paper.hall_ticket}
                        </td>
                        <td className="py-2.5 px-3 text-[#b0b4be]">
                          {paper.student_name || '—'}
                        </td>
                        <td className="py-2.5 px-3 text-center font-mono">
                          <span className="text-[#ededed] font-medium">{paper.total_score}</span>
                          <span className="text-[#555963]"> / {paper.max_total}</span>
                        </td>
                        <td className="py-2.5 px-3 text-center">
                          <ConfidenceBadge
                            routing={hasFlagged ? 'FLAGGED' : 'AUTO_ACCEPT'}
                            score={paper.avg_confidence}
                            size="sm"
                          />
                        </td>
                        <td className="py-2.5 px-3 text-center font-mono text-[11px]">
                          {hasFlagged ? (
                            <span className="text-[#f59e0b]">
                              {paper.flagged_count} Flagged
                            </span>
                          ) : (
                            <span className="text-[#4ade80]">
                              Complete
                            </span>
                          )}
                        </td>
                        <td className="py-2.5 px-3 text-right" onClick={(e) => e.stopPropagation()}>
                          {hasFlagged && onGoToReview ? (
                            <button
                              onClick={() => onGoToReview(paper.hall_ticket)}
                              className="text-xs font-mono text-[#f59e0b] hover:text-[#fbbf24] underline cursor-pointer"
                            >
                              Review →
                            </button>
                          ) : (
                            <span className="text-[#4d515a] font-mono text-[11px]">—</span>
                          )}
                        </td>
                      </tr>

                      {/* Expandable Per-Question Breakdown */}
                      {isExpanded && (
                        <tr className="bg-[#0e0f12] border-y border-[#1f2126]">
                          <td colSpan={7} className="p-4 space-y-3">
                            <div className="flex items-center justify-between border-b border-[#1c1e23] pb-1.5 font-mono text-[11px]">
                              <span className="text-[#8c9099] uppercase">
                                Questions: {paper.hall_ticket}
                              </span>
                              <span className="text-[#555963]">
                                {paper.questions?.length || 0} Questions
                              </span>
                            </div>

                            <div className="space-y-3">
                              {(paper.questions || []).map((q) => (
                                <div key={q.question_number} className="bg-[#121316] rounded p-3 border border-[#202228] space-y-2">
                                  <div className="flex flex-wrap items-center justify-between gap-2 font-mono text-xs">
                                    <div className="flex items-center gap-2">
                                      <span className="text-[#ededed] font-semibold">
                                        Q{q.question_number}
                                      </span>
                                      <span className="text-[#717680] text-[10px] border border-[#272a31] px-1 rounded">
                                        {q.question_type}
                                      </span>
                                      <ConfidenceBadge routing={q.routing} score={q.c_final} size="sm" />
                                    </div>

                                    <div>
                                      <span className="text-[#717680]">Score: </span>
                                      <span className="text-[#ededed] font-medium">{q.final_score}</span>
                                      <span className="text-[#555963]"> / {q.max_marks}</span>
                                    </div>
                                  </div>

                                  <EvidenceTable criteria={q.criteria} questionNumber={q.question_number} />
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
