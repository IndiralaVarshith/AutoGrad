import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, ArrowRight, BadgeCheck, CheckCheck, CheckCircle2, ChevronLeft, ChevronRight, Flag, GraduationCap, ImageOff, Inbox, Loader2, Maximize,
  Minus, Plus, RotateCcw, Save, ShieldCheck, Sparkles, Trash2,
} from 'lucide-react';
import api from '../api';
import {
  Button, Callout, CountUp, EmptyState, Pill, ProgressBar, Skeleton, Tip, cx, errorText,
} from '../components/ui';
import { useFeedback } from '../components/feedback';

const NEEDS_DECISION = new Set(['FLAGGED', 'MANUAL']);

const REASON_TEXT = {
  UNREADABLE: 'Could not be read',
  UNMATCHED: 'Did not match a question',
  MISSING: 'No answer found',
  LOW_CONFIDENCE: 'AutoGrade was unsure',
  PARTIAL_HIGH_VALUE: 'Partial marks: AutoGrade is less reliable on these',
  ZERO_ON_WRITTEN: 'Zero on an answer the student did write',
  EVIDENCE_NOT_FOUND: 'Quoted evidence is not in the answer',
  INVALID_MARK: 'AutoGrade gave an impossible mark',
  UNCLEAR_OPTION: 'Option choice is unclear',
  NO_KEY: 'No approved key for this question',
  DUPLICATE_ANSWER: 'Answered more than once',
  NUMBER_INFERRED: 'Student wrote no question number',
  ATTEMPT_RULE: 'Answered more than the paper allows',
  SHEET_NOT_READ: 'AutoGrade could not read this part of the sheet',
};

const answerKey = (q) => `${q.question_number}:${q.sub_part || ''}`;
const label = (q) => `Q${q.question_number}${q.sub_part || ''}`;
const reasons = (q) => (q.flag_reason || '').split(',').filter(Boolean);
const isTyping = (el) => el && (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable);

function orderForReview(questions) {
  const flagged = questions.filter((q) => NEEDS_DECISION.has(q.routing));
  const rest = questions.filter((q) => !NEEDS_DECISION.has(q.routing));
  return [...flagged, ...rest];
}

function sheetRank(paper) {
  if (paper.finality_status === 'FINAL') return 2;
  return paper.flagged_count > 0 ? 0 : 1;
}

function MarkPicker({ value, max, suggested, onChange }) {
  const whole = Number(max) > 0 && Number(max) <= 10 ? Array.from({ length: Math.floor(Number(max)) + 1 }, (_, i) => i) : [];
  const nudge = (delta) => {
    const next = (Number(value) || 0) + delta;
    onChange(String(Math.min(Number(max), Math.max(0, next))));
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5">
        {whole.map((mark) => {
          const chosen = value !== '' && Number(value) === mark;
          return (
            <button
              key={mark}
              type="button"
              aria-pressed={chosen}
              onClick={() => onChange(String(mark))}
              className={cx(
                'relative grid size-10 place-items-center rounded-xl border text-sm font-semibold tabular-nums transition-[background-color,border-color,color,transform] duration-150 active:scale-95',
                chosen ? 'border-primary bg-primary text-on-primary' : 'border-line-strong hover:bg-sunken',
              )}
            >
              {mark}
              {suggested === mark && !chosen && (
                <span className="absolute -top-1 -right-1 size-2.5 rounded-full border-2 border-surface bg-warn" aria-label="AutoGrade's suggestion" />
              )}
            </button>
          );
        })}
        <div className="inline-flex items-center rounded-xl border border-line-strong">
          <button type="button" onClick={() => nudge(-0.5)} aria-label="Half a mark less" className="icon-btn size-10 rounded-r-none">
            <Minus size={15} aria-hidden="true" />
          </button>
          <input
            type="number"
            step="0.5"
            min="0"
            max={max}
            value={value}
            aria-label={`Marks, out of ${max}`}
            onChange={(e) => onChange(e.target.value)}
            className="h-10 w-12 bg-transparent text-center text-sm font-semibold tabular-nums focus:outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
          />
          <button type="button" onClick={() => nudge(0.5)} aria-label="Half a mark more" className="icon-btn size-10 rounded-l-none">
            <Plus size={15} aria-hidden="true" />
          </button>
        </div>
      </div>
      {suggested !== null && suggested !== undefined && whole.includes(Number(suggested)) && (
        <p className="hint flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-warn" aria-hidden="true" />
          The dot shows AutoGrade's suggestion of {suggested}.
        </p>
      )}
    </div>
  );
}

function AnswerEditor({ examId, paper, question, busy, draft, onDecide }) {
  const originalText = question.teacher_corrected_text || question.extracted_text || '';
  const startMark = question.routing === 'AUTO_ACCEPT'
    ? question.final_score
    : (question.suggested_score ?? '');
  // A save that failed comes back with what the teacher typed, so nothing has to be redone.
  const [text, setText] = useState(draft?.text ?? originalText);
  const [marks, setMarks] = useState(draft?.marks ?? (startMark === '' ? '' : String(startMark)));
  const [note, setNote] = useState(draft?.note ?? '');
  const [error, setError] = useState('');
  const needsDecision = NEEDS_DECISION.has(question.routing);
  const canAccept = needsDecision && question.suggested_score !== null && question.suggested_score !== undefined;

  const save = () => {
    const value = Number(marks);
    if (marks === '' || Number.isNaN(value)) {
      setError('Choose a mark first.');
      return;
    }
    if (value < 0 || value > Number(question.max_marks)) {
      setError(`The mark must be between 0 and ${question.max_marks}.`);
      return;
    }
    setError('');
    onDecide({
      work: () => api.saveAnswerEdit({
        exam_id: examId,
        hall_ticket: paper.hall_ticket,
        question_number: Number(question.question_number),
        sub_part: question.sub_part || '',
        new_score: value,
        edit_reason: note,
        corrected_text: text === originalText ? null : text,
      }),
      draft: { text, marks, note },
    });
  };

  const quote = question.criteria?.[0]?.verbatim_quote;
  return (
    <div
      className="page-enter space-y-4"
      onKeyDown={(e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
          e.preventDefault();
          if (!busy) save();
        }
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-2xl">{label(question)}</h2>
          <p className="text-sm text-muted">Worth {question.max_marks} {Number(question.max_marks) === 1 ? 'mark' : 'marks'}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          {needsDecision
            ? <Pill tone="warn" icon={Flag}>Needs your decision</Pill>
            : <Pill tone="good" icon={CheckCircle2}>Decided</Pill>}
          <Tip text="How sure AutoGrade was. This does not reliably predict accuracy, so unflagged marks can still be wrong.">
            <span tabIndex={0} className="cursor-help rounded text-[13px] text-muted underline decoration-dotted underline-offset-2 tabular-nums">
              AutoGrade's confidence: {Math.round((question.c_final || 0) * 100)}%
            </span>
          </Tip>
        </div>
      </div>

      {(reasons(question).length > 0 || question.routing_reason) && (
        <div className="rounded-xl bg-warn-soft px-3.5 py-2.5 text-sm">
          <div className="font-semibold text-warn">{needsDecision ? 'Why this was flagged' : 'Note'}</div>
          {reasons(question).length > 0 && (
            <ul className="mt-0.5 list-disc pl-4">
              {reasons(question).map((reason) => <li key={reason}>{REASON_TEXT[reason] || reason}</li>)}
            </ul>
          )}
          {question.routing_reason && <p className="mt-1 text-muted">{question.routing_reason}</p>}
        </div>
      )}

      {quote && (
        <blockquote className="border-l-2 border-line-strong pl-3 text-sm">
          <span className="block text-[13px] font-semibold text-muted">Evidence AutoGrade quoted</span>
          {quote}
        </blockquote>
      )}

      <div>
        <label htmlFor="student-text" className="label">What the student wrote</label>
        <textarea
          id="student-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          className="input input-grow block max-h-72 min-h-24 text-sm leading-relaxed"
        />
        <p className="hint">If AutoGrade misread something, correct it here.</p>
      </div>

      <div>
        <div className="label">Marks</div>
        <MarkPicker value={marks} max={question.max_marks} suggested={question.suggested_score ?? null} onChange={setMarks} />
      </div>

      <div>
        <label htmlFor="teacher-note" className="label">Note <span className="font-normal text-muted">(optional)</span></label>
        <input id="teacher-note" value={note} onChange={(e) => setNote(e.target.value)} className="input text-sm" placeholder="Why you changed this mark" />
        <p className="hint">If you calibrate with this sheet, later sheets follow your note.</p>
      </div>

      {error && <p role="alert" className="fade text-sm font-medium text-bad">{error}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" icon={Save} loading={busy} onClick={save}>Save my marks</Button>
        {canAccept && (
          <Button
            icon={Sparkles}
            disabled={busy}
            onClick={() => onDecide({
              work: () => api.confirmQuestion(
                examId, paper.hall_ticket, question.question_number, question.sub_part || '',
              ),
              draft: { text, marks, note },
            })}
          >
            Accept AutoGrade's {question.suggested_score}
          </Button>
        )}
      </div>
    </div>
  );
}

function PageViewer({ examId, paper, page, onPage }) {
  const [zoom, setZoom] = useState(100);
  const [state, setState] = useState('loading');
  const box = useRef(null);
  const drag = useRef(null);
  const src = api.getReviewSourcePageUrl(examId, paper.hall_ticket, page);
  const hasNext = !paper.page_count || page < paper.page_count;

  useLayoutEffect(() => {
    setState('loading');
    if (box.current) box.current.scrollTop = 0;
  }, [src]);

  // Have the neighbouring pages ready, so turning a page is instant.
  useEffect(() => {
    [page - 1, page + 1].forEach((neighbour) => {
      if (neighbour >= 1 && (!paper.page_count || neighbour <= paper.page_count)) {
        new Image().src = api.getReviewSourcePageUrl(examId, paper.hall_ticket, neighbour);
      }
    });
  }, [examId, paper.hall_ticket, paper.page_count, page]);

  const startDrag = (e) => {
    if (zoom === 100 || !box.current) return;
    drag.current = { x: e.clientX, y: e.clientY, left: box.current.scrollLeft, top: box.current.scrollTop };
    box.current.setPointerCapture(e.pointerId);
  };
  const moveDrag = (e) => {
    if (!drag.current) return;
    box.current.scrollLeft = drag.current.left - (e.clientX - drag.current.x);
    box.current.scrollTop = drag.current.top - (e.clientY - drag.current.y);
  };

  return (
    <div className="card overflow-hidden lg:sticky lg:top-20">
      <div className="flex items-center justify-between gap-2 border-b border-line px-2 py-1.5">
        <div className="flex items-center">
          <button type="button" className="icon-btn" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
            <ChevronLeft size={18} aria-hidden="true" />
          </button>
          <span className="px-1 text-sm font-medium tabular-nums" aria-live="polite">
            Page {page}{paper.page_count ? ` of ${paper.page_count}` : ''}
          </span>
          <button type="button" className="icon-btn" disabled={!hasNext} onClick={() => onPage(page + 1)} aria-label="Next page">
            <ChevronRight size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="flex items-center">
          <button type="button" className="icon-btn" disabled={zoom <= 100} onClick={() => setZoom(zoom - 25)} aria-label="Zoom out">
            <Minus size={16} aria-hidden="true" />
          </button>
          <span className="w-11 text-center text-[13px] text-muted tabular-nums">{zoom}%</span>
          <button type="button" className="icon-btn" disabled={zoom >= 300} onClick={() => setZoom(zoom + 25)} aria-label="Zoom in">
            <Plus size={16} aria-hidden="true" />
          </button>
          <button type="button" className="icon-btn" disabled={zoom === 100} onClick={() => setZoom(100)} aria-label="Fit to width">
            <Maximize size={15} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div
        ref={box}
        onPointerDown={startDrag}
        onPointerMove={moveDrag}
        onPointerUp={() => { drag.current = null; }}
        onPointerCancel={() => { drag.current = null; }}
        className={cx(
          'relative h-[65dvh] overflow-auto bg-sunken lg:h-[calc(100dvh-12rem)]',
          zoom > 100 && 'cursor-grab touch-none select-none active:cursor-grabbing',
        )}
      >
        {state === 'loading' && <Skeleton className="absolute inset-3" />}
        {state === 'error' ? (
          <div className="grid h-full place-items-center p-6 text-center text-sm text-muted">
            <div>
              <ImageOff size={28} className="mx-auto mb-2" aria-hidden="true" />
              Could not load this page. There may be no page {page}.
            </div>
          </div>
        ) : (
          <img
            key={src}
            src={src}
            draggable={false}
            alt={`Page ${page} of the sheet for ${paper.hall_ticket}`}
            onLoad={() => setState('ready')}
            onError={() => setState('error')}
            className={cx('mx-auto block max-w-none bg-white transition-opacity duration-200', state === 'ready' ? 'opacity-100' : 'opacity-0')}
            style={{ width: `${zoom}%` }}
          />
        )}
      </div>
    </div>
  );
}

function QuestionRail({ questions, current, saving, onChoose }) {
  const box = useRef(null);
  const flaggedCount = questions.filter((q) => NEEDS_DECISION.has(q.routing)).length;

  // Keep the chosen question in view inside the list, without moving the page.
  useEffect(() => {
    const list = box.current;
    const item = list?.querySelector('[aria-current="true"]');
    if (!item) return;
    const outer = list.getBoundingClientRect();
    const inner = item.getBoundingClientRect();
    const top = inner.top < outer.top ? inner.top - outer.top - 32 : inner.bottom > outer.bottom ? inner.bottom - outer.bottom + 8 : 0;
    const left = inner.left < outer.left ? inner.left - outer.left - 8 : inner.right > outer.right ? inner.right - outer.right + 8 : 0;
    if (top || left) list.scrollBy({ top, left, behavior: 'smooth' });
  }, [current]);

  return (
    <ul ref={box} className="card flex gap-1 overflow-x-auto p-1.5 lg:block lg:max-h-[calc(100dvh-11rem)] lg:space-y-0.5 lg:overflow-x-visible lg:overflow-y-auto">
      {questions.map((q, index) => {
        const flagged = NEEDS_DECISION.has(q.routing);
        const chosen = answerKey(q) === current;
        return (
          <React.Fragment key={answerKey(q)}>
            {index === 0 && flaggedCount > 0 && (
              <li className="hidden px-2 pt-1 pb-1 text-xs font-semibold text-warn lg:block">To decide ({flaggedCount})</li>
            )}
            {index === flaggedCount && flaggedCount < questions.length && (
              <li className={cx('hidden px-2 pb-1 text-xs font-semibold text-muted lg:block', flaggedCount > 0 ? 'pt-3' : 'pt-1')}>
                Decided ({questions.length - flaggedCount})
              </li>
            )}
            <li className="shrink-0">
              <button
                type="button"
                aria-current={chosen}
                onClick={() => onChoose(answerKey(q))}
                className={cx(
                  'flex w-full items-center justify-between gap-3 rounded-[0.6rem] px-2.5 py-2 text-left text-sm transition-colors duration-150',
                  chosen ? 'bg-primary-soft font-semibold' : 'hover:bg-sunken',
                )}
              >
                <span className="flex items-center gap-1.5">
                  {saving[answerKey(q)]
                    ? <Loader2 size={13} className="spin text-primary" aria-label="Saving" />
                    : flagged
                      ? <Flag size={13} className="text-warn" aria-label="Needs your decision" />
                      : <CheckCircle2 size={13} className="text-good" aria-label="Decided" />}
                  {label(q)}
                </span>
                <span className="text-[13px] whitespace-nowrap text-muted tabular-nums">
                  {flagged ? (q.suggested_score ?? '?') : q.routing === 'NOT_ATTEMPTED' ? '-' : q.final_score}/{q.max_marks}
                </span>
              </button>
            </li>
          </React.Fragment>
        );
      })}
    </ul>
  );
}

/** One marked sheet teaches the app this teacher's marking, for the sheets marked after it. */
function CalibrationCard({ paper, calibration, ready, busy, onUse, onRemark, onStop }) {
  const active = Boolean(calibration?.active);
  const current = active && calibration.current;
  const isSource = active && calibration.hall_ticket === paper.hall_ticket;
  const remarkCount = current ? (calibration.can_remark?.length || 0) : 0;
  const changes = (count) => `${count} ${count === 1 ? 'mark' : 'marks'} you changed`;

  return (
    <div className={cx('card space-y-3 p-4', isSource && current && 'card-highlight')}>
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary-soft text-primary">
          <GraduationCap size={18} aria-hidden="true" />
        </span>
        <div className="min-w-0 text-sm">
          <h3 className="text-xl leading-tight">Calibration</h3>
          {isSource && current ? (
            <p className="mt-0.5">
              This sheet is your calibration: {calibration.answers} written answers, {changes(calibration.corrections)}
              {calibration.remarks ? `, ${calibration.remarks} with notes` : ''}. New sheets are marked the way you marked it.
            </p>
          ) : current ? (
            <p className="mt-0.5">
              New sheets are marked to your calibration from <strong>{calibration.hall_ticket}</strong> ({changes(calibration.corrections)}).
            </p>
          ) : active ? (
            <p className="mt-0.5 text-warn">
              Your calibration from {calibration.hall_ticket} was made with older approved marks, so new sheets do not use it. Choose a sheet again.
            </p>
          ) : (
            <p className="mt-0.5 text-muted">
              Optional. Decide every flagged answer here, change any mark you disagree with and say why in the note.
              Then use this sheet to teach the app: sheets marked after it follow your marking.
            </p>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {!(isSource && current) && (
          <Tip text={ready ? 'Approves this sheet and learns how you marked it' : 'Decide every flagged answer on this sheet first'}>
            <Button size="sm" variant={current ? 'secondary' : 'primary'} icon={GraduationCap} loading={busy} disabled={!ready} onClick={onUse}>
              {current ? 'Use this sheet instead' : 'Use this sheet to calibrate'}
            </Button>
          </Tip>
        )}
        {remarkCount > 0 && (
          <Tip text="Only sheets you have not touched are marked again.">
            <Button size="sm" icon={RotateCcw} onClick={() => onRemark(remarkCount)}>
              Mark {remarkCount} {remarkCount === 1 ? 'sheet' : 'sheets'} again
            </Button>
          </Tip>
        )}
        {active && <Button size="sm" variant="ghost" onClick={onStop}>Stop calibration</Button>}
      </div>
    </div>
  );
}

export default function Review({ examId = '', openRequest = null, onBackToDashboard, onGoToUpload, onResults, active = true }) {
  const [papers, setPapers] = useState([]);
  const [ticket, setTicket] = useState(openRequest?.ticket ?? null);
  const [selected, setSelected] = useState('');
  const [page, setPage] = useState(1);
  const [unmatchedChecked, setUnmatchedChecked] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const { toast, confirm } = useFeedback();
  const [saving, setSaving] = useState({}); // "sheet|question" while its save is on the way
  const [failedDraft, setFailedDraft] = useState(null);
  const [calibration, setCalibration] = useState(null);
  const [calibrating, setCalibrating] = useState(false);
  const saveQueue = useRef(Promise.resolve());

  const load = useCallback(async () => {
    if (!examId) {
      setPapers([]);
      setLoading(false);
      return;
    }
    try {
      const [res, calibrated] = await Promise.all([
        api.getExamResults(examId),
        api.getCalibration(examId).catch(() => null),
      ]);
      const sorted = [...(res.results || [])].sort((a, b) => sheetRank(a) - sheetRank(b));
      setPapers(sorted);
      setCalibration(calibrated);
      onResults?.(sorted);
      setError('');
    } catch {
      setError('Could not load the sheets. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [examId, onResults]);

  // A different exam starts clean, rather than showing the last exam's sheets.
  useEffect(() => {
    setLoading(true);
    setPapers([]);
    setCalibration(null);
    setTicket(null);
    setSelected('');
  }, [examId]);

  // This page stays mounted while hidden, so it reloads each time the teacher opens it.
  useEffect(() => {
    if (active) load();
  }, [load, active]);

  // Each "open this sheet" from Results counts, even when it is the same sheet as last time.
  useEffect(() => {
    if (openRequest) {
      setTicket(openRequest.ticket);
      setSelected('');
      setPage(1);
      setUnmatchedChecked(false);
    }
  }, [openRequest]);

  // Stay on the sheet in hand. Sheets re-sort as flags clear, and that must never swap
  // the sheet a teacher is about to approve for a different one.
  useEffect(() => {
    if (papers.length > 0 && !papers.some((item) => item.hall_ticket === ticket)) {
      setTicket(papers[0].hall_ticket);
    }
  }, [papers, ticket]);

  const paper = papers.find((item) => item.hall_ticket === ticket) || papers[0];
  const questions = useMemo(() => orderForReview(paper?.questions || []), [paper]);
  const question = questions.find((q) => answerKey(q) === selected) || questions[0];
  const blanks = questions.filter((q) => q.routing === 'MANUAL' && q.flag_reason === 'MISSING').length;
  const unmatched = paper?.unmapped_answers || [];
  const questionId = question ? answerKey(question) : '';
  const questionPage = question?.source_page_references?.[0]?.page_number;

  // Before the browser paints, so the wrong page never flashes up first.
  useLayoutEffect(() => {
    if (questionPage) setPage(questionPage);
  }, [questionId, questionPage, paper?.hall_ticket]);

  const chooseSheet = useCallback((hallTicket) => {
    setTicket(hallTicket);
    setSelected('');
    setPage(1);
    setUnmatchedChecked(false);
  }, []);

  // Move around without reaching for the mouse. Keys only move: a decision always takes a click.
  useEffect(() => {
    if (!active || !paper) return undefined;
    const onKey = (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target) || document.querySelector('dialog[open]')) return;
      const at = questions.findIndex((q) => answerKey(q) === questionId);
      if (e.key === 'j' && at < questions.length - 1) setSelected(answerKey(questions[at + 1]));
      else if (e.key === 'k' && at > 0) setSelected(answerKey(questions[at - 1]));
      else if (e.key === 'ArrowRight' && (!paper.page_count || page < paper.page_count)) setPage(page + 1);
      else if (e.key === 'ArrowLeft' && page > 1) setPage(page - 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [active, paper, questions, questionId, page]);

  // The teacher moves on at once. Saves go one at a time, so two can never overwrite each other.
  const decide = ({ work, draft }) => {
    const sheet = paper.hall_ticket;
    const key = questionId;
    const id = `${sheet}|${key}`;
    setSaving((now) => ({ ...now, [id]: true }));
    setFailedDraft(null);
    const next = questions.find((q) => (
      NEEDS_DECISION.has(q.routing) && answerKey(q) !== key && !saving[`${sheet}|${answerKey(q)}`]
    ));
    if (next) setSelected(answerKey(next));

    saveQueue.current = saveQueue.current.then(async () => {
      try {
        await work();
        await load();
      } catch (err) {
        toast.error(`${sheet}, question ${key.split(':').join('')}: ${errorText(err, 'That did not save.')} Nothing was changed.`);
        setFailedDraft({ id, ...draft });
        setTicket(sheet);
        setSelected(key);
      } finally {
        setSaving((now) => {
          const rest = { ...now };
          delete rest[id];
          return rest;
        });
      }
    });
  };

  const run = async (work, done) => {
    try {
      setError('');
      const result = await work();
      await load();
      toast.success(done(result));
    } catch (err) {
      toast.error(errorText(err, 'That did not save. Please try again.'));
    }
  };

  const remark = async (count) => {
    const goAhead = await confirm({
      title: `Mark ${count} ${count === 1 ? 'sheet' : 'sheets'} again to your calibration?`,
      body: 'Only sheets you have not touched are marked again. Their marks are replaced when the new marks arrive.',
      confirmLabel: 'Mark them again',
    });
    if (!goAhead) return;
    run(() => api.remarkWithCalibration(examId), (r) => [
      `${r.queued.length} ${r.queued.length === 1 ? 'sheet is' : 'sheets are'} being marked again. Watch the progress on Upload.`,
      r.kept.length ? `${r.kept.length} you already worked on stay as they are.` : '',
      r.missing.length ? `${r.missing.length} could not be found: upload ${r.missing.length === 1 ? 'it' : 'them'} again.` : '',
    ].filter(Boolean).join(' '));
  };

  const calibrateWithThisSheet = async () => {
    try {
      setCalibrating(true);
      const res = await api.calibrateWithSheet(examId, paper.hall_ticket, unmatchedChecked);
      await load();
      toast.success(`Calibrated from ${paper.hall_ticket}: ${res.answers} written answers, ${res.corrections} ${res.corrections === 1 ? 'mark' : 'marks'} you changed.`);
      if (res.can_remark?.length) await remark(res.can_remark.length);
    } catch (err) {
      toast.error(errorText(err, 'Could not use this sheet to calibrate.'));
    } finally {
      setCalibrating(false);
    }
  };

  const stopCalibrating = async () => {
    const goAhead = await confirm({
      title: 'Stop using the calibration?',
      body: 'Sheets marked from now on use the approved marks only. Marks already given stay as they are.',
      confirmLabel: 'Stop calibration',
    });
    if (goAhead) run(() => api.stopCalibration(examId), () => 'Calibration stopped.');
  };

  const deleteThisSheet = async () => {
    const goAhead = await confirm({
      title: `Delete the sheet “${paper.hall_ticket}”?`,
      body: 'Its marks, review notes and stored pages are deleted. If it is your calibration sheet, the calibration stops too. It cannot be undone.',
      confirmLabel: 'Delete sheet',
      danger: true,
    });
    if (!goAhead) return;
    const gone = paper.hall_ticket;
    run(() => api.deleteSheet(examId, gone), (r) => `Deleted ${gone}.${r.calibration_removed ? ' Its calibration was removed too.' : ''}`);
  };

  if (loading) {
    return (
      <div className="mx-auto grid max-w-[90rem] gap-4 px-4 py-8 sm:px-6 lg:grid-cols-[11rem_1fr_1fr]" aria-label="Loading sheets">
        <Skeleton className="h-96" />
        <Skeleton className="h-[32rem]" />
        <Skeleton className="h-[32rem]" />
      </div>
    );
  }
  if (error && !paper) {
    return (
      <EmptyState icon={AlertTriangle} title="Could not load the sheets" action={<Button onClick={load}>Try again</Button>}>
        {error}
      </EmptyState>
    );
  }
  if (!paper) {
    return (
      <EmptyState
        icon={Inbox}
        title={examId ? 'Nothing to review yet' : 'Choose an exam first'}
        action={<Button variant="primary" onClick={onGoToUpload}>Add answer sheets</Button>}
      >
        {examId
          ? `No sheets have been marked for ${examId}. Once they are, flagged answers appear here first.`
          : 'Use the Exam ID menu at the top of the page.'}
      </EmptyState>
    );
  }

  const savesPending = Object.keys(saving).length > 0;
  const sheetSaving = Object.fromEntries(Object.keys(saving)
    .filter((id) => id.startsWith(`${paper.hall_ticket}|`))
    .map((id) => [id.slice(paper.hall_ticket.length + 1), true]));
  const canApprove = !savesPending && !paper.flagged_count && (unmatched.length === 0 || unmatchedChecked);
  const isFinal = paper.finality_status === 'FINAL';
  const decided = questions.filter((q) => !NEEDS_DECISION.has(q.routing)).length;
  const at = papers.findIndex((item) => item.hall_ticket === paper.hall_ticket);
  const nextOpen = papers.find((item) => item.finality_status !== 'FINAL' && item.hall_ticket !== paper.hall_ticket);
  const unflaggedSheets = papers.filter((item) => item.finality_status !== 'FINAL' && !item.flagged_count).length;
  const readyToTeach = !savesPending && (isFinal || canApprove);
  const isCalibrationSheet = calibration?.active && calibration.hall_ticket === paper.hall_ticket;

  return (
    <div className="mx-auto max-w-[90rem] space-y-4 px-4 py-6 sm:px-6">
      <div className="card flex flex-wrap items-center gap-x-5 gap-y-3 px-3 py-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-1">
          <button type="button" className="icon-btn" disabled={at <= 0} onClick={() => chooseSheet(papers[at - 1].hall_ticket)} aria-label="Previous sheet">
            <ChevronLeft size={18} aria-hidden="true" />
          </button>
          <label className="min-w-0">
            <span className="sr-only">Sheet</span>
            <select
              value={paper.hall_ticket}
              onChange={(e) => chooseSheet(e.target.value)}
              className="input min-h-9 max-w-52 py-1 text-sm font-semibold sm:max-w-72"
            >
              {papers.map((item) => (
                <option key={item.hall_ticket} value={item.hall_ticket}>
                  {item.hall_ticket}
                  {item.finality_status === 'FINAL' ? ' (final)' : item.flagged_count ? ` (${item.flagged_count} flagged)` : ' (no flags)'}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="icon-btn" disabled={at >= papers.length - 1} onClick={() => chooseSheet(papers[at + 1].hall_ticket)} aria-label="Next sheet">
            <ChevronRight size={18} aria-hidden="true" />
          </button>
          <Tip text="Delete this sheet">
            <button type="button" className="icon-btn icon-btn-danger" disabled={savesPending} onClick={deleteThisSheet} aria-label={`Delete the sheet ${paper.hall_ticket}`}>
              <Trash2 size={17} aria-hidden="true" />
            </button>
          </Tip>
        </div>

        <div className="min-w-44 flex-1">
          <div className="mb-1 flex justify-between gap-3 text-[13px] text-muted">
            <span>{decided} of {questions.length} answers decided</span>
            <span className="tabular-nums">
              Total so far: <strong className="text-ink"><CountUp value={Number(paper.total_score) || 0} decimals={Number.isInteger(Number(paper.total_score)) ? 0 : 1} /></strong> of {paper.max_total}
            </span>
          </div>
          <ProgressBar value={questions.length ? (decided / questions.length) * 100 : 0} label="Answers decided on this sheet" />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {isCalibrationSheet && <Pill tone="primary" icon={GraduationCap}>Calibration sheet</Pill>}
          {!isCalibrationSheet && paper.calibrated_from && (
            <Tip text={`Marked to your calibration from ${paper.calibrated_from}`}>
              <span tabIndex={0}><Pill tone="primary" icon={GraduationCap}>Calibrated</Pill></span>
            </Tip>
          )}
          {isFinal ? <Pill tone="good" icon={BadgeCheck}>Final</Pill> : <Pill tone="warn">Not final</Pill>}
          <Button
            size="sm"
            icon={CheckCheck}
            disabled={unflaggedSheets === 0 || savesPending}
            onClick={async () => {
              const goAhead = await confirm({
                title: `Approve ${unflaggedSheets} ${unflaggedSheets === 1 ? 'sheet' : 'sheets'} with no flags?`,
                body: 'Unflagged marks can still be wrong, so spot-check some first. Approved sheets become final.',
                confirmLabel: 'Approve them',
              });
              if (goAhead) {
                run(() => api.approveUnflagged(examId), (r) => (
                  `Approved ${r.approved.length} sheets with no flags. ${r.still_need_review.length} still need review.`
                ));
              }
            }}
          >
            <span className="hidden sm:inline">Approve all sheets with no flags</span>
            <span className="sm:hidden">Approve unflagged</span>
          </Button>
        </div>
      </div>

      {error && <Callout tone="bad" onDismiss={() => setError('')}>{error}</Callout>}

      {paper.sheet_problems?.length > 0 && (
        <Callout tone="bad" title="Problems with this sheet">
          <ul className="list-disc pl-4">
            {paper.sheet_problems.map((problem) => <li key={problem}>{problem}</li>)}
          </ul>
        </Callout>
      )}

      <div className="grid items-start gap-4 lg:grid-cols-[11rem_minmax(0,1.05fr)_minmax(22rem,1fr)]">
        <div className="min-w-0 space-y-2 lg:sticky lg:top-20">
          <QuestionRail questions={questions} current={questionId} saving={sheetSaving} onChoose={setSelected} />
          <p className="hidden px-1 text-xs leading-relaxed text-muted lg:block">
            <kbd className="font-sans font-semibold">J</kbd> / <kbd className="font-sans font-semibold">K</kbd> change question.
            {' '}<kbd className="font-sans font-semibold">←</kbd> / <kbd className="font-sans font-semibold">→</kbd> turn page.
          </p>
        </div>

        <PageViewer examId={examId} paper={paper} page={page} onPage={setPage} />

        <div className="min-w-0 space-y-4">
          {blanks > 0 && (
            <Callout
              tone="warn"
              title={`${blanks} ${blanks === 1 ? 'answer has' : 'answers have'} no writing found`}
              action={(
                <Button
                  size="sm"
                  disabled={savesPending}
                  onClick={() => run(() => api.confirmBlanks(examId, paper.hall_ticket), (r) => `${r.confirmed.length} blanks set to 0.`)}
                >
                  I checked: {blanks} {blanks === 1 ? 'blank gets' : 'blanks get'} 0
                </Button>
              )}
            >
              A blank is never marked 0 by itself. Check the pages first.
            </Callout>
          )}

          {question && (
            <div className="card p-5">
              <AnswerEditor
                key={`${paper.hall_ticket}:${answerKey(question)}:${question.routing}`}
                examId={examId}
                paper={paper}
                question={question}
                busy={Boolean(sheetSaving[answerKey(question)])}
                draft={failedDraft?.id === `${paper.hall_ticket}|${answerKey(question)}` ? failedDraft : null}
                onDecide={decide}
              />
            </div>
          )}

          {unmatched.length > 0 && (
            <div className="card space-y-2 p-4">
              <h3 className="flex items-center gap-2 text-base text-warn">
                <AlertTriangle size={17} aria-hidden="true" />
                Writing that fit no question
              </h3>
              <ul className="space-y-1.5 text-sm">
                {unmatched.map((item, index) => (
                  <li key={`${index}-${item.page_references?.[0]?.page_number}`} className="rounded-lg bg-sunken px-3 py-2">
                    {item.detected_question_number ? <strong>Written as Q{item.detected_question_number}: </strong> : ''}
                    {item.extracted_text}
                    {item.page_references?.[0]?.page_number && (
                      <button type="button" className="ml-1.5 font-medium text-primary underline" onClick={() => setPage(item.page_references[0].page_number)}>
                        page {item.page_references[0].page_number}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              <label className="flex items-center gap-2.5 pt-1 text-sm font-medium">
                <input type="checkbox" className="check" checked={unmatchedChecked} onChange={(e) => setUnmatchedChecked(e.target.checked)} />
                I have checked this
              </label>
            </div>
          )}

          <div className={cx('card flex flex-wrap items-center justify-between gap-3 p-4 transition-colors duration-300', isFinal && 'border-good bg-good-soft')}>
            <div className="min-w-0 text-sm">
              {isFinal ? (
                <span className="flex items-center gap-2 font-semibold text-good">
                  <BadgeCheck size={18} className="pop" aria-hidden="true" />
                  This sheet is final
                </span>
              ) : savesPending ? (
                <span className="flex items-center gap-2 text-muted">
                  <Loader2 size={15} className="spin" aria-hidden="true" />
                  Saving your decisions
                </span>
              ) : paper.flagged_count > 0 ? (
                <span className="text-muted">{paper.flagged_count} flagged {paper.flagged_count === 1 ? 'answer still needs' : 'answers still need'} a decision.</span>
              ) : unmatched.length > 0 && !unmatchedChecked ? (
                <span className="text-muted">Check the writing that fit no question first.</span>
              ) : (
                <span className="font-medium">All decided. Ready to approve.</span>
              )}
            </div>
            {isFinal ? (
              nextOpen && (
                <Button variant="primary" onClick={() => chooseSheet(nextOpen.hall_ticket)}>
                  Next sheet
                  <ArrowRight size={16} aria-hidden="true" />
                </Button>
              )
            ) : (
              <Button
                variant="primary"
                icon={ShieldCheck}
                disabled={!canApprove}
                onClick={() => run(() => api.approveSheet(examId, paper.hall_ticket, unmatchedChecked), () => `${paper.hall_ticket} is now final.`)}
              >
                Approve this sheet
              </Button>
            )}
          </div>

          <CalibrationCard
            paper={paper}
            calibration={calibration}
            ready={readyToTeach}
            busy={calibrating}
            onUse={calibrateWithThisSheet}
            onRemark={remark}
            onStop={stopCalibrating}
          />

          {isFinal && !nextOpen && onBackToDashboard && (
            <Button className="w-full" variant="primary" onClick={onBackToDashboard}>
              Every sheet is final. See the results
              <ArrowRight size={16} aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
