import React, { useState, useEffect } from 'react';
import api from '../api';
import ConfidenceBadge from '../components/ConfidenceBadge';
import EvidenceTable from '../components/EvidenceTable';

export default function Review({ examId = 'EXAM_2026_01', initialHallTicket = null, onBackToDashboard }) {
  const [currentExamId, setCurrentExamId] = useState(examId);
  const [flaggedPapers, setFlaggedPapers] = useState([]);
  const [currentPaperIndex, setCurrentPaperIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [editStates, setEditStates] = useState({});
  const [statusMessage, setStatusMessage] = useState('');

  const loadFlaggedPapers = async () => {
    try {
      setLoading(true);
      const res = await api.getExamResults(currentExamId);
      const allResults = res.results || [];
      const flagged = allResults.filter(
        (p) => p.status === 'NEEDS_REVIEW' || (p.flagged_count && p.flagged_count > 0)
      );

      setFlaggedPapers(flagged);

      if (initialHallTicket) {
        const foundIdx = flagged.findIndex((p) => p.hall_ticket === initialHallTicket);
        if (foundIdx !== -1) setCurrentPaperIndex(foundIdx);
      }
    } catch (err) {
      console.error('Failed to load flagged papers:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFlaggedPapers();
  }, [currentExamId]);

  const activePaper = flaggedPapers[currentPaperIndex];

  const handleConfirm = async (questionNumber) => {
    if (!activePaper) return;
    try {
      setActionLoading(questionNumber);
      setStatusMessage('');
      await api.confirmQuestion(currentExamId, activePaper.hall_ticket, questionNumber);

      setStatusMessage(`Question Q${questionNumber} confirmed.`);
      await loadFlaggedPapers();
    } catch (err) {
      console.error('Confirm error:', err);
      alert(err.response?.data?.detail || 'Failed to confirm question.');
    } finally {
      setActionLoading(null);
    }
  };

  const handleEditSubmit = async (questionNumber) => {
    if (!activePaper) return;
    const editState = editStates[questionNumber];
    if (!editState || editState.score === '') {
      alert('Please enter a valid numeric score.');
      return;
    }

    try {
      setActionLoading(questionNumber);
      setStatusMessage('');
      await api.editQuestionScore(
        currentExamId,
        activePaper.hall_ticket,
        questionNumber,
        Number(editState.score),
        editState.reason || 'Faculty override'
      );

      setStatusMessage(`Question Q${questionNumber} score updated to ${editState.score}.`);
      setEditStates((prev) => ({
        ...prev,
        [questionNumber]: { ...prev[questionNumber], open: false },
      }));
      await loadFlaggedPapers();
    } catch (err) {
      console.error('Edit error:', err);
      alert(err.response?.data?.detail || 'Failed to override score.');
    } finally {
      setActionLoading(null);
    }
  };

  const toggleEditForm = (qNum, currentScore) => {
    setEditStates((prev) => ({
      ...prev,
      [qNum]: {
        score: prev[qNum]?.score ?? String(currentScore),
        reason: prev[qNum]?.reason ?? '',
        open: !prev[qNum]?.open,
      },
    }));
  };

  if (loading && flaggedPapers.length === 0) {
    return (
      <div className="max-w-4xl mx-auto py-16 text-center text-xs font-mono text-[#717680]">
        Loading flagged papers...
      </div>
    );
  }

  if (flaggedPapers.length === 0) {
    return (
      <div className="max-w-md mx-auto py-16 px-4 text-center space-y-4">
        <div className="text-sm font-semibold text-[#4ade80] font-mono uppercase tracking-wider">
          Review Queue Clean
        </div>
        <p className="text-xs text-[#8c9099] leading-relaxed">
          No papers in exam <span className="font-mono text-[#ededed]">{currentExamId}</span> currently require manual verification.
        </p>
        <div className="pt-2">
          <button
            onClick={onBackToDashboard}
            className="bg-[#181a1d] hover:bg-[#202227] text-[#c4c7cf] border border-[#272a31] py-2 px-4 rounded text-xs font-mono transition-colors cursor-pointer"
          >
            ← Return to Results
          </button>
        </div>
      </div>
    );
  }

  const flaggedQuestions = (activePaper?.questions || []).filter(
    (q) => (q.routing === 'FLAGGED' || q.routing === 'MANUAL') && !q.teacher_confirmed
  );

  return (
    <div className="max-w-5xl mx-auto py-6 px-4 sm:px-6 space-y-5">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-[#22242a] pb-4">
        <div className="flex items-center gap-3">
          <button
            onClick={onBackToDashboard}
            className="px-2.5 py-1 rounded bg-[#16181d] hover:bg-[#202227] text-[#8c9099] border border-[#25282f] text-xs font-mono transition-colors"
          >
            ← Back
          </button>
          <div>
            <h1 className="text-lg font-bold text-[#ededed] tracking-tight">
              Evaluation Review
            </h1>
            <p className="text-xs text-[#717680] font-mono">
              Manual verification for disputed criteria and low-confidence markings
            </p>
          </div>
        </div>

        {/* Paper Pagination */}
        <div className="flex items-center gap-2 font-mono text-xs bg-[#121316] border border-[#22242a] px-2.5 py-1 rounded">
          <button
            disabled={currentPaperIndex === 0}
            onClick={() => setCurrentPaperIndex((prev) => prev - 1)}
            className="text-[#8c9099] hover:text-[#ededed] disabled:opacity-30 cursor-pointer"
          >
            ← Prev
          </button>
          <span className="text-[#717680]">
            {currentPaperIndex + 1} / {flaggedPapers.length}
          </span>
          <button
            disabled={currentPaperIndex === flaggedPapers.length - 1}
            onClick={() => setCurrentPaperIndex((prev) => prev + 1)}
            className="text-[#8c9099] hover:text-[#ededed] disabled:opacity-30 cursor-pointer"
          >
            Next →
          </button>
        </div>
      </div>

      {statusMessage && (
        <div className="p-2.5 rounded bg-[#112217] border border-[#1b3825] text-[#4ade80] text-xs font-mono">
          {statusMessage}
        </div>
      )}

      {/* Active Student Banner */}
      <div className="bg-[#121316] border border-[#22242a] rounded p-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono font-semibold text-[#ededed]">
              {activePaper.hall_ticket}
            </span>
            <span className="text-xs text-[#8c9099]">
              {activePaper.student_name || 'Candidate'}
            </span>
          </div>
          <p className="text-[11px] font-mono text-[#555963] mt-0.5">
            Total Score: {activePaper.total_score} / {activePaper.max_total}
          </p>
        </div>

        <div className="text-right font-mono text-xs text-[#f59e0b]">
          {flaggedQuestions.length} flagged questions
        </div>
      </div>

      {/* Flagged Questions List */}
      <div className="space-y-4">
        {flaggedQuestions.map((q) => {
          const qNum = q.question_number;
          const editState = editStates[qNum] || { score: String(q.final_score), reason: '', open: false };
          const isActing = actionLoading === qNum;

          return (
            <div key={qNum} className="bg-[#121316] border border-[#22242a] rounded p-5 space-y-4">
              {/* Question Header */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#1c1e23] pb-2.5">
                <div className="flex items-center gap-2 font-mono text-xs">
                  <span className="font-semibold text-[#ededed]">
                    Question {qNum}
                  </span>
                  <span className="text-[#717680] text-[10px] border border-[#272a31] px-1 rounded">
                    {q.question_type}
                  </span>
                  <ConfidenceBadge routing={q.routing} score={q.c_final} size="sm" />
                </div>

                <div className="flex items-center gap-3 font-mono text-xs">
                  <div>
                    <span className="text-[#717680]">Score: </span>
                    <span className="text-[#ededed] font-medium">{q.final_score}</span>
                    <span className="text-[#555963]"> / {q.max_marks}</span>
                  </div>
                  <div className="text-[#555963] text-[11px]">
                    (Grader: {q.grader_score} | Critic: {q.critic_score})
                  </div>
                </div>
              </div>

              {/* OCR Text Box */}
              <div className="bg-[#0c0d0e] rounded p-3 border border-[#1f2126]">
                <div className="text-[10px] font-mono uppercase tracking-wider text-[#717680] mb-1">
                  Transcribed Student Text
                </div>
                <div className="text-xs font-mono text-[#c4c7cf] whitespace-pre-wrap leading-relaxed max-h-36 overflow-y-auto">
                  {q.extracted_text || <span className="text-[#555963] italic">No text extracted</span>}
                </div>
              </div>

              {/* Evidence Table */}
              <div className="space-y-1">
                <div className="text-[10px] font-mono uppercase tracking-wider text-[#717680]">
                  Criteria & Evidence Assessment
                </div>
                <EvidenceTable criteria={q.criteria} questionNumber={qNum} />
              </div>

              {/* Review Decision Actions */}
              <div className="pt-2 border-t border-[#1c1e23] flex flex-wrap items-center justify-between gap-3">
                <div className="text-[11px] font-mono text-[#717680]">
                  {q.routing === 'FLAGGED' && 'Flagged due to critic challenge or citation match ratio.'}
                  {q.routing === 'MANUAL' && 'Low transcript clarity requires faculty evaluation.'}
                </div>

                <div className="flex items-center gap-2 font-mono">
                  <button
                    type="button"
                    disabled={isActing}
                    onClick={() => toggleEditForm(qNum, q.final_score)}
                    className="px-3 py-1 rounded text-xs bg-[#1a1c21] hover:bg-[#22242a] text-[#c4c7cf] border border-[#2b2e36] transition-colors cursor-pointer"
                  >
                    Edit Score
                  </button>

                  <button
                    type="button"
                    disabled={isActing}
                    onClick={() => handleConfirm(qNum)}
                    className="px-3.5 py-1 rounded text-xs bg-[#15803d] hover:bg-[#166534] text-white transition-colors cursor-pointer disabled:opacity-50"
                  >
                    {isActing ? 'Saving...' : `Confirm Score (${q.final_score})`}
                  </button>
                </div>
              </div>

              {/* Edit Score Drawer */}
              {editState.open && (
                <div className="p-3 bg-[#0c0d0e] rounded border border-[#25282f] space-y-2.5 font-mono">
                  <div className="text-xs text-[#ededed]">
                    Override Score for Q{qNum}
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <div>
                      <label className="block text-[10px] text-[#717680] mb-0.5">New Score (Max {q.max_marks})</label>
                      <input
                        type="number"
                        step="0.5"
                        min="0"
                        max={q.max_marks}
                        value={editState.score}
                        onChange={(e) =>
                          setEditStates((prev) => ({
                            ...prev,
                            [qNum]: { ...prev[qNum], score: e.target.value },
                          }))
                        }
                        className="w-full bg-[#121316] border border-[#22242a] rounded px-2 py-1 text-xs text-[#ededed] focus:outline-none"
                      />
                    </div>

                    <div className="sm:col-span-2">
                      <label className="block text-[10px] text-[#717680] mb-0.5">Reason for Override</label>
                      <input
                        type="text"
                        value={editState.reason}
                        onChange={(e) =>
                          setEditStates((prev) => ({
                            ...prev,
                            [qNum]: { ...prev[qNum], reason: e.target.value },
                          }))
                        }
                        placeholder="Handwritten solution steps valid"
                        className="w-full bg-[#121316] border border-[#22242a] rounded px-2 py-1 text-xs text-[#ededed] focus:outline-none"
                      />
                    </div>
                  </div>

                  <div className="flex justify-end gap-2 pt-1">
                    <button
                      type="button"
                      onClick={() => toggleEditForm(qNum, q.final_score)}
                      className="px-2.5 py-0.5 rounded text-xs text-[#717680] hover:text-[#ededed]"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      disabled={isActing}
                      onClick={() => handleEditSubmit(qNum)}
                      className="px-3 py-0.5 rounded text-xs bg-[#2563eb] hover:bg-[#1d4ed8] text-white cursor-pointer"
                    >
                      Save Override
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
