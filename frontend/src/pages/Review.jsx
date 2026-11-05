import React, { useCallback, useEffect, useMemo, useState } from 'react';
import api from '../api';

const NEEDS_DECISION = new Set(['FLAGGED', 'MANUAL']);

const REASON_TEXT = {
  UNREADABLE: 'Could not be read',
  UNMATCHED: 'Did not match a question',
  MISSING: 'No answer found',
  LOW_CONFIDENCE: 'Gemini was unsure',
  PARTIAL_HIGH_VALUE: 'Partial marks: Gemini is less reliable on these',
  ZERO_ON_WRITTEN: 'Zero on an answer the student did write',
  EVIDENCE_NOT_FOUND: 'Quoted evidence is not in the answer',
  INVALID_MARK: 'Gemini gave an impossible mark',
  UNCLEAR_OPTION: 'Option choice is unclear',
  NO_KEY: 'No approved key for this question',
  DUPLICATE_ANSWER: 'Answered more than once',
  NUMBER_INFERRED: 'Student wrote no question number',
  ATTEMPT_RULE: 'Answered more than the paper allows',
  SHEET_NOT_READ: 'Gemini could not read this part of the sheet',
};

const answerKey = (q) => `${q.question_number}:${q.sub_part || ''}`;
const label = (q) => `Q${q.question_number}${q.sub_part || ''}`;
const reasons = (q) => (q.flag_reason || '').split(',').filter(Boolean);

function orderForReview(questions) {
  const flagged = questions.filter((q) => NEEDS_DECISION.has(q.routing));
  const rest = questions.filter((q) => !NEEDS_DECISION.has(q.routing));
  return [...flagged, ...rest];
}

function sheetRank(paper) {
  if (paper.finality_status === 'FINAL') return 2;
  return paper.flagged_count > 0 ? 0 : 1;
}

function AnswerEditor({ examId, paper, question, onSaved }) {
  const originalText = question.teacher_corrected_text ?? question.extracted_text ?? '';
  const startMark = question.routing === 'AUTO_ACCEPT'
    ? question.final_score
    : (question.suggested_score ?? '');
  const [text, setText] = useState(originalText);
  const [marks, setMarks] = useState(startMark);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const needsDecision = NEEDS_DECISION.has(question.routing);
  const canAccept = needsDecision && question.suggested_score !== null && question.suggested_score !== undefined;

  const act = async (work) => {
    try {
      setSaving(true);
      setError('');
      await work();
      await onSaved();
    } catch (err) {
      setError(err.response?.data?.detail || 'That did not save. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const save = () => {
    if (marks === '' || Number.isNaN(Number(marks))) {
      setError('Enter a mark first.');
      return;
    }
    act(() => api.saveAnswerEdit({
      exam_id: examId,
      hall_ticket: paper.hall_ticket,
      question_number: Number(question.question_number),
      sub_part: question.sub_part || '',
      new_score: Number(marks),
      edit_reason: note,
      corrected_text: text === originalText ? null : text,
    }));
  };

  const quote = question.criteria?.[0]?.verbatim_quote;
  return (
    <div className="space-y-3 text-xs">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-[#e6e8ec]">
          {label(question)} <span className="font-normal text-[#8c9099]">out of {question.max_marks}</span>
        </h3>
        <span className="text-[#8c9099]">Gemini's confidence: {Math.round((question.c_final || 0) * 100)}%</span>
      </div>

      {reasons(question).length > 0 && (
        <ul className="space-y-1 text-[#f59e0b]">
          {reasons(question).map((reason) => <li key={reason}>{REASON_TEXT[reason] || reason}</li>)}
        </ul>
      )}

      {question.routing_reason && <p className="text-[#a0a4ad]">Reason: {question.routing_reason}</p>}
      {quote && <p className="text-[#a0a4ad]">Evidence: "{quote}"</p>}

      {question.second_opinion && (
        <p className="border border-[#2e323b] rounded p-2 text-[#a0a4ad]">
          Second opinion ({question.second_opinion.model}): {question.second_opinion.marks ?? 'no mark'} marks.{' '}
          {question.second_opinion.reason}
        </p>
      )}

      <label className="block text-[#8c9099]">
        What the student wrote
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={6}
          className="mt-1 w-full rounded border border-[#2e323b] bg-[#0c0d0e] p-2 text-[#e6e8ec]"
        />
      </label>

      <div className="flex flex-wrap gap-3">
        <label className="text-[#8c9099]">
          Marks
          <input
            type="number"
            step="0.5"
            min="0"
            max={question.max_marks}
            value={marks}
            onChange={(e) => setMarks(e.target.value)}
            className="ml-2 w-20 rounded border border-[#2e323b] bg-[#0c0d0e] p-1.5 text-[#e6e8ec]"
          />
        </label>
        <label className="flex-1 min-w-48 text-[#8c9099]">
          Note (optional)
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="ml-2 w-3/4 rounded border border-[#2e323b] bg-[#0c0d0e] p-1.5 text-[#e6e8ec]"
          />
        </label>
      </div>

      {error && <p className="text-[#ef4444]">{String(error)}</p>}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="rounded bg-[#2563eb] px-3 py-1.5 font-medium text-white cursor-pointer disabled:opacity-50"
        >
          Save my marks
        </button>
        {canAccept && (
          <button
            type="button"
            disabled={saving}
            onClick={() => act(() => api.confirmQuestion(
              examId, paper.hall_ticket, question.question_number, question.sub_part || '',
            ))}
            className="rounded border border-[#2e323b] px-3 py-1.5 text-[#d6d9e0] cursor-pointer disabled:opacity-50"
          >
            Accept Gemini's {question.suggested_score}
          </button>
        )}
      </div>
    </div>
  );
}

export default function Review({ examId = '', initialHallTicket = null, onBackToDashboard }) {
  const [papers, setPapers] = useState([]);
  const [ticket, setTicket] = useState(initialHallTicket);
  const [selected, setSelected] = useState('');
  const [page, setPage] = useState(1);
  const [unmatchedChecked, setUnmatchedChecked] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!examId) {
      setPapers([]);
      setLoading(false);
      return;
    }
    try {
      const res = await api.getExamResults(examId);
      const sorted = [...(res.results || [])].sort((a, b) => sheetRank(a) - sheetRank(b));
      setPapers(sorted);
      setError('');
    } catch {
      setError('Could not load the sheets. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [examId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (initialHallTicket) setTicket(initialHallTicket);
  }, [initialHallTicket]);

  const paper = papers.find((item) => item.hall_ticket === ticket) || papers[0];
  const questions = useMemo(() => orderForReview(paper?.questions || []), [paper]);
  const question = questions.find((q) => answerKey(q) === selected) || questions[0];
  const blanks = questions.filter((q) => q.routing === 'MANUAL' && q.flag_reason === 'MISSING').length;
  const unmatched = paper?.unmapped_answers || [];

  useEffect(() => {
    const ref = question?.source_page_references?.[0]?.page_number;
    if (ref) setPage(ref);
  }, [question]);

  const chooseSheet = (hallTicket) => {
    setTicket(hallTicket);
    setSelected('');
    setPage(1);
    setUnmatchedChecked(false);
    setMessage('');
  };

  const run = async (work, done) => {
    try {
      setError('');
      const result = await work();
      await load();
      setMessage(done(result));
    } catch (err) {
      setError(err.response?.data?.detail || 'That did not save. Please try again.');
    }
  };

  if (loading) return <p className="text-xs text-[#8c9099]">Loading sheets...</p>;
  if (!paper) {
    return (
      <div className="space-y-2 text-xs text-[#8c9099]">
        <p>No graded sheets for this exam yet.</p>
        {onBackToDashboard && (
          <button type="button" onClick={onBackToDashboard} className="underline cursor-pointer">
            Back to dashboard
          </button>
        )}
      </div>
    );
  }

  const canApprove = !paper.flagged_count && (unmatched.length === 0 || unmatchedChecked);
  const isFinal = paper.finality_status === 'FINAL';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <label className="text-[#8c9099]">
          Sheet
          <select
            value={paper.hall_ticket}
            onChange={(e) => chooseSheet(e.target.value)}
            className="ml-2 rounded border border-[#2e323b] bg-[#0c0d0e] p-1.5 text-[#e6e8ec]"
          >
            {papers.map((item) => (
              <option key={item.hall_ticket} value={item.hall_ticket}>
                {item.hall_ticket}
                {item.finality_status === 'FINAL' ? ' (final)' : item.flagged_count ? ` (${item.flagged_count} flagged)` : ' (no flags)'}
              </option>
            ))}
          </select>
        </label>
        <span className="text-[#e6e8ec]">Total so far: {paper.total_score} of {paper.max_total}</span>
        <span className={isFinal ? 'text-[#22c55e]' : 'text-[#f59e0b]'}>{isFinal ? 'Final' : 'Not final'}</span>
        <button
          type="button"
          onClick={() => run(() => api.approveUnflagged(examId), (r) => (
            `Approved ${r.approved.length} sheets with no flags. ${r.still_need_review.length} still need review.`
          ))}
          className="ml-auto rounded border border-[#2e323b] px-3 py-1.5 text-[#d6d9e0] cursor-pointer"
          title="Unflagged marks can still be wrong. Spot-check some first."
        >
          Approve all sheets with no flags
        </button>
      </div>

      {message && <p className="text-xs text-[#22c55e]">{message}</p>}
      {error && <p className="text-xs text-[#ef4444]">{String(error)}</p>}

      {paper.sheet_problems?.length > 0 && (
        <ul className="rounded border border-[#ef4444]/50 p-2 text-xs text-[#ef4444]">
          {paper.sheet_problems.map((problem) => <li key={problem}>{problem}</li>)}
        </ul>
      )}

      <div className="grid gap-4 lg:grid-cols-[14rem_1fr_1fr]">
        <div className="space-y-2">
          {blanks > 0 && (
            <button
              type="button"
              onClick={() => run(() => api.confirmBlanks(examId, paper.hall_ticket), (r) => `${r.confirmed.length} blanks set to 0.`)}
              className="w-full rounded border border-[#2e323b] px-2 py-1.5 text-xs text-[#d6d9e0] cursor-pointer"
              title="Check the pages first: this gives 0 to every answer flagged as not found."
            >
              I checked: {blanks} blanks get 0
            </button>
          )}
          <ul className="max-h-[70vh] overflow-y-auto rounded border border-[#22242a] divide-y divide-[#22242a] text-xs">
            {questions.map((q) => {
              const flagged = NEEDS_DECISION.has(q.routing);
              const active = answerKey(q) === answerKey(question);
              return (
                <li key={answerKey(q)}>
                  <button
                    type="button"
                    onClick={() => setSelected(answerKey(q))}
                    className={`flex w-full justify-between gap-2 px-2 py-1.5 text-left cursor-pointer ${active ? 'bg-[#1c1e23]' : ''}`}
                  >
                    <span className={flagged ? 'text-[#f59e0b]' : 'text-[#c8ccd4]'}>{label(q)}{flagged ? ' •' : ''}</span>
                    <span className="text-[#8c9099]">
                      {flagged ? (q.suggested_score ?? '?') : q.routing === 'NOT_ATTEMPTED' ? '-' : q.final_score} / {q.max_marks}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs text-[#8c9099]">
            <span>Scanned page {page}{paper.page_count ? ` of ${paper.page_count}` : ''}</span>
            <span className="space-x-2">
              <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className="underline cursor-pointer disabled:opacity-40">Previous</button>
              <button type="button" disabled={Boolean(paper.page_count) && page >= paper.page_count} onClick={() => setPage(page + 1)} className="underline cursor-pointer disabled:opacity-40">Next</button>
            </span>
          </div>
          <img
            src={api.getReviewSourcePageUrl(examId, paper.hall_ticket, page)}
            alt={`Page ${page} of the sheet for ${paper.hall_ticket}`}
            className="w-full rounded border border-[#22242a] bg-white"
          />
        </div>

        <div className="space-y-4">
          {question && (
            <AnswerEditor
              key={`${paper.hall_ticket}:${answerKey(question)}:${question.routing}`}
              examId={examId}
              paper={paper}
              question={question}
              onSaved={load}
            />
          )}

          {unmatched.length > 0 && (
            <div className="space-y-2 rounded border border-[#f59e0b]/40 p-2 text-xs">
              <p className="font-medium text-[#f59e0b]">Writing that fit no question</p>
              <ul className="space-y-1 text-[#c8ccd4]">
                {unmatched.map((item, index) => (
                  <li key={`${index}-${item.page_references?.[0]?.page_number}`}>
                    {item.detected_question_number ? `Written as Q${item.detected_question_number}: ` : ''}
                    {item.extracted_text}
                    {item.page_references?.[0]?.page_number ? ` (page ${item.page_references[0].page_number})` : ''}
                  </li>
                ))}
              </ul>
              <label className="flex items-center gap-2 text-[#a0a4ad]">
                <input type="checkbox" checked={unmatchedChecked} onChange={(e) => setUnmatchedChecked(e.target.checked)} />
                I have checked this
              </label>
            </div>
          )}

          <div className="border-t border-[#22242a] pt-3">
            <button
              type="button"
              disabled={!canApprove || isFinal}
              onClick={() => run(() => api.approveSheet(examId, paper.hall_ticket, unmatchedChecked), () => `${paper.hall_ticket} is now final.`)}
              className="rounded bg-[#16a34a] px-4 py-2 text-xs font-medium text-white cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isFinal ? 'Approved' : 'Approve this sheet'}
            </button>
            {paper.flagged_count > 0 && (
              <p className="mt-1 text-xs text-[#8c9099]">{paper.flagged_count} flagged answers still need a decision.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
