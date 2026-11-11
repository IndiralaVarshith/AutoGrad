import React, { useState, useEffect, useRef } from 'react';
import { ArrowLeft, ArrowRight, Check, CheckCircle2, Minus, Plus, RefreshCw, ShieldCheck, Sparkles, Trash2, X } from 'lucide-react';
import api from '../api';
import {
  Button, Callout, CountUp, Disclosure, Dropzone, FoldingCallout, Pill, PageHeader, Segmented, Tip, cx, errorText,
} from '../components/ui';
import { useFeedback } from '../components/feedback';

const STEP_NAMES = ['Add the paper', 'Check the marks', 'Ready'];

const PAPER_TYPES = [
  { value: 'WRITTEN', label: 'Handwritten answers' },
  { value: 'MCQ', label: 'Multiple choice' },
  { value: 'MIXED', label: 'Both' },
];
const PAPER_TYPE_HINT = {
  WRITTEN: 'Theory, diagrams, graphs and maps. Each answer is marked point by point.',
  MCQ: 'Only option letters are read from the sheet. This is the quickest and uses the fewest tokens.',
  MIXED: 'Option letters are checked against the key, and written answers are marked point by point.',
};
const MARKING_STYLES = [
  { value: 'GENEROUS', label: 'Generous' },
  { value: 'BALANCED', label: 'Balanced' },
  { value: 'STRICT', label: 'Strict' },
];
const MARKING_STYLE_HINT = {
  GENEROUS: 'Full marks when the student shows the idea, even briefly. Spelling loses nothing.',
  BALANCED: 'Full marks for a point stated clearly and correctly, half when it is vague.',
  STRICT: 'Marks come off for misspelt key terms, missing labels and incomplete points. Long answers rarely get full marks.',
};

function Stepper({ step, canReview, onStep }) {
  return (
    <ol className="mb-6 flex items-center gap-2">
      {STEP_NAMES.map((name, index) => {
        const number = index + 1;
        const done = number < step;
        const active = number === step;
        const reachable = number === 1 || (number === 2 && canReview);
        return (
          <React.Fragment key={name}>
            {index > 0 && (
              <li aria-hidden="true" className="h-0.5 max-w-16 flex-1 overflow-hidden rounded-full bg-line">
                <div className={cx('h-full origin-left bg-primary transition-transform duration-500 ease-out', number <= step ? 'scale-x-100' : 'scale-x-0')} />
              </li>
            )}
            <li>
              <button
                type="button"
                disabled={!reachable}
                onClick={() => onStep(number)}
                aria-current={active ? 'step' : undefined}
                className={cx(
                  'flex items-center gap-2 rounded-lg text-sm font-semibold transition-colors disabled:cursor-default',
                  active ? 'text-ink' : done ? 'text-primary' : 'text-muted',
                )}
              >
                <span
                  className={cx(
                    'grid size-6 place-items-center rounded-full text-xs font-bold transition-colors duration-300',
                    done || active ? 'bg-primary text-on-primary' : 'bg-line text-muted',
                  )}
                >
                  {done ? <Check size={13} strokeWidth={3} aria-hidden="true" /> : number}
                </span>
                <span className={active ? '' : 'hidden sm:inline'}>{name}</span>
              </button>
            </li>
          </React.Fragment>
        );
      })}
    </ol>
  );
}

function ExamIdInput({ value, onCommit }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  return (
    <div className="max-w-md">
      <label htmlFor="setup-exam-id" className="label">Exam ID <span className="text-bad">*</span></label>
      <input
        id="setup-exam-id"
        value={draft}
        required
        placeholder="For example MATHS-MID-2026"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => onCommit(draft)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            onCommit(draft);
          }
        }}
        className="input"
      />
      <p className="hint">A short name you will recognise later. Use a new one for each exam.</p>
    </div>
  );
}

function MarksStepper({ value, onChange, label }) {
  const nudge = (delta) => onChange(Math.max(0, (Number(value) || 0) + delta));
  return (
    <div className="inline-flex items-center rounded-[0.6rem] border border-line-strong bg-surface">
      <button type="button" onClick={() => nudge(-0.5)} aria-label={`Lower ${label}`} className="icon-btn size-8 rounded-r-none">
        <Minus size={14} aria-hidden="true" />
      </button>
      <input
        type="number"
        step="0.5"
        min="0"
        value={value}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 w-11 bg-transparent text-center text-sm font-semibold tabular-nums focus:outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
      />
      <button type="button" onClick={() => nudge(0.5)} aria-label={`Raise ${label}`} className="icon-btn size-8 rounded-l-none">
        <Plus size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

function SummaryTile({ label, children }) {
  return (
    <div className="rounded-xl bg-sunken px-4 py-3">
      <div className="text-[13px] text-muted">{label}</div>
      <div className="mt-0.5 text-xl font-semibold">{children}</div>
    </div>
  );
}

function QuestionCard({ q, qIdx, index, handlers }) {
  const criteriaSum = q.criteria.reduce((s, c) => s + (Number(c.max_marks) || 0), 0);
  const marksMatch = Math.abs(criteriaSum - Number(q.max_marks)) <= 0.05;

  return (
    <article className="card lazy-card rise p-4 sm:p-5" style={{ '--i': Math.min(index, 8) }}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <div className="flex shrink-0 flex-wrap items-center gap-1.5 pt-1.5">
          <span className="font-display text-lg font-semibold">
            Q{q.question_number}{q.sub_part ? `(${q.sub_part})` : ''}
          </span>
          {q.section && <Pill>{q.section}</Pill>}
          {q.choice_group && <Pill tone="info">Choice</Pill>}
        </div>
        <textarea
          rows={1}
          value={q.question_text}
          aria-label={`Text of question ${q.question_number}`}
          onChange={(e) => handlers.questionText(qIdx, e.target.value)}
          className="input input-quiet input-grow min-w-0 flex-1 text-sm"
        />
        <div className="flex shrink-0 flex-wrap items-center gap-2 text-[13px] text-muted">
          Marks
          <MarksStepper value={q.max_marks} label={`marks for question ${q.question_number}`} onChange={(v) => handlers.maxMarks(qIdx, v)} />
        </div>
      </div>

      {q.question_format === 'MCQ' && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-muted">
          Correct answer
          <div className="flex flex-wrap gap-1">
            {(q.options || []).map((option) => (
              <button
                key={option.label}
                type="button"
                title={option.text}
                aria-pressed={q.correct_option === option.label}
                onClick={() => handlers.correctOption(qIdx, q.correct_option === option.label ? '' : option.label)}
                className={cx(
                  'grid size-8 place-items-center rounded-lg border text-sm font-semibold transition-colors',
                  q.correct_option === option.label
                    ? 'border-primary bg-primary text-on-primary'
                    : 'border-line-strong text-ink hover:bg-sunken',
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          {q.suggested_option && !q.correct_option && (
            <Pill tone="warn">Gemini suggests {q.suggested_option}. Please confirm.</Pill>
          )}
          {!q.correct_option && !q.suggested_option && <Pill>Not set</Pill>}
        </div>
      )}

      <div className="mt-3 space-y-1.5">
        {q.criteria.map((c, cIdx) => (
          <div key={cIdx} className="group flex items-start gap-2 rounded-xl bg-sunken p-1.5">
            <textarea
              rows={1}
              value={c.description}
              aria-label={`Marking point ${cIdx + 1}`}
              onChange={(e) => handlers.criterion(qIdx, cIdx, 'description', e.target.value)}
              className="input input-quiet input-grow min-w-0 flex-1 text-sm"
            />
            <MarksStepper value={c.max_marks} label={`marks for marking point ${cIdx + 1}`} onChange={(v) => handlers.criterion(qIdx, cIdx, 'max_marks', v)} />
            <button type="button" onClick={() => handlers.removeCriterion(qIdx, cIdx)} aria-label={`Remove marking point ${cIdx + 1}`} className="icon-btn size-8 hover:text-bad!">
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <Button variant="ghost" size="sm" icon={Plus} onClick={() => handlers.addCriterion(qIdx)}>Add marking point</Button>
        {marksMatch
          ? <Pill tone="good" icon={CheckCircle2}>Points add up: {criteriaSum} of {Number(q.max_marks)}</Pill>
          : <Pill tone="warn">Points add up to {criteriaSum}, but the question is worth {Number(q.max_marks)}</Pill>}
      </div>
    </article>
  );
}

export default function Setup({ onExamApproved, onExamIdChange, examId = '' }) {
  const [step, setStep] = useState(1);
  const [questionFile, setQuestionFile] = useState(null);
  const [textbookFile, setTextbookFile] = useState(null);
  const [paperKeyFile, setPaperKeyFile] = useState(null);
  const [paperType, setPaperType] = useState('MIXED');
  const [markingStyle, setMarkingStyle] = useState('GENEROUS');
  const [markingNotes, setMarkingNotes] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const { toast, confirm } = useFeedback();

  // Rubric state for Step 2
  const [rubricQuestions, setRubricQuestions] = useState([]);
  const [extractedSummary, setExtractedSummary] = useState(null);
  const [genFingerprint, setGenFingerprint] = useState('');
  const [docFingerprint, setDocFingerprint] = useState('');
  const [hasUnsavedEdits, setHasUnsavedEdits] = useState(false);
  const [questionsChecked, setQuestionsChecked] = useState(false);
  // Which exam the state above belongs to, so one exam's draft is never saved under another's ID.
  const [loadedExam, setLoadedExam] = useState('');
  const loadToken = useRef(0);

  // Load this exam's draft, or its approved rubric. Nothing from the last exam carries over.
  useEffect(() => {
    const cleanId = examId.trim();
    loadToken.current += 1;
    const token = loadToken.current;

    setStep(1);
    setRubricQuestions([]);
    setExtractedSummary(null);
    setGenFingerprint('');
    setDocFingerprint('');
    setHasUnsavedEdits(false);
    setQuestionsChecked(false);
    setLoadedExam('');
    if (!cleanId) return;

    // 1. A draft still in progress in this tab
    try {
      const rawDraft = sessionStorage.getItem(`autograde_setup_${cleanId}`);
      const draft = rawDraft ? JSON.parse(rawDraft) : null;
      if (draft?.rubricQuestions?.length > 0) {
        setStep(draft.step || 2);
        setRubricQuestions(draft.rubricQuestions);
        setExtractedSummary(draft.extractedSummary || null);
        setGenFingerprint(draft.genFingerprint || '');
        setDocFingerprint(draft.docFingerprint || '');
        setHasUnsavedEdits(Boolean(draft.hasUnsavedEdits));
        setLoadedExam(cleanId);
        return;
      }
    } catch (e) {
      console.warn('[Setup] Failed to parse sessionStorage draft:', e);
    }

    // 2. A rubric that is already approved
    api.getExamRubric(cleanId)
      .then((existing) => {
        if (token !== loadToken.current || !existing?.questions?.length) return;
        setStep(2);
        setRubricQuestions(existing.questions);
        setMarkingStyle(existing.marking_style || 'GENEROUS');
        setMarkingNotes(existing.marking_notes || '');
        setExtractedSummary({
          total_marks: existing.total_marks || existing.questions.reduce((s, q) => s + (q.max_marks || 0), 0),
          declared_maximum_marks: existing.total_marks,
          calculated_maximum_marks: existing.total_marks,
          validation_status: 'VALID',
          sections: existing.sections || [],
          warnings: [],
          reconciliation_notes: ['Loaded the rubric a teacher already approved.'],
          from_cache: true,
          is_approved: true,
          version: existing.version || 1,
          rubric_id: existing.rubric_id || `${cleanId}_v1`,
          questions_count: existing.questions.length,
        });
      })
      .catch(() => {
        // No approved rubric yet: the teacher can generate one.
      })
      .finally(() => {
        if (token === loadToken.current) setLoadedExam(cleanId);
      });
  }, [examId]);

  // Keep the draft through a page reload.
  useEffect(() => {
    const cleanId = examId.trim();
    if (!cleanId || loadedExam !== cleanId) return;
    try {
      if (rubricQuestions.length > 0 || extractedSummary) {
        sessionStorage.setItem(`autograde_setup_${cleanId}`, JSON.stringify({
          step,
          examId: cleanId,
          rubricQuestions,
          extractedSummary,
          genFingerprint,
          docFingerprint,
          hasUnsavedEdits,
        }));
      }
    } catch {
      // ignore quota errors
    }
  }, [step, examId, loadedExam, rubricQuestions, extractedSummary, genFingerprint, docFingerprint, hasUnsavedEdits]);

  // Step 1: Upload question paper & generate proposed rubric
  const handleGenerateRubric = async (e, forceRegenerate = false) => {
    if (e?.preventDefault) e.preventDefault();
    if (!examId.trim()) {
      toast.error('Please enter an Exam ID first.');
      return;
    }
    if (!questionFile) {
      toast.error(forceRegenerate
        ? 'To read the paper again, add it on the first step.'
        : 'Please add the question paper (PDF, image or text).');
      if (forceRegenerate) setStep(1);
      return;
    }

    try {
      setLoading(true);

      const formData = new FormData();
      formData.append('exam_id', examId.trim());
      formData.append('question_paper', questionFile);
      if (textbookFile) {
        formData.append('textbook', textbookFile);
      }
      formData.append('force_regenerate', forceRegenerate ? 'true' : 'false');
      formData.append('paper_type', paperType);
      if (paperKeyFile) {
        formData.append('answer_key', paperKeyFile);
      }

      const res = await api.setupExam(formData);

      const questions = res.rubric?.questions || res.questions || [];
      if (questions.length === 0) {
        toast.error('No questions were found in that file. Please check it is the question paper.');
        return;
      }

      const totalMarks = res.rubric?.total_marks !== undefined
        ? Number(res.rubric.total_marks)
        : (res.rubric?.calculated_maximum_marks || questions.reduce((s, q) => s + (q.max_marks || 0), 0));

      setRubricQuestions(questions);
      setGenFingerprint(res.gen_fingerprint || '');
      setDocFingerprint(res.doc_fingerprint || '');
      setExtractedSummary({
        total_marks: totalMarks,
        declared_maximum_marks: res.rubric?.declared_maximum_marks ?? null,
        calculated_maximum_marks: res.rubric?.calculated_maximum_marks ?? totalMarks,
        validation_status: res.rubric?.validation_status || 'VALID',
        reconciliation_notes: res.rubric?.reconciliation_notes || [],
        sections: res.rubric?.sections || [],
        warnings: [...(res.problems || []), ...(res.rubric?.extraction_warnings || [])],
        review_items: res.rubric?.review_items || [],
        from_cache: res.from_cache || false,
        source_kind: res.rubric?.source_kind || '',
        printed_total: res.rubric?.source_kind === 'gemini' ? (res.rubric?.declared_maximum_marks ?? null) : null,
        questions_count: questions.length,
        schema_version: res.rubric?.schema_version || '',
        is_approved: false,
      });

      setHasUnsavedEdits(false);
      setQuestionsChecked(false);
      setLoadedExam(examId.trim());
      setStep(2);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      console.error('Setup error:', err);
      toast.error(errorText(err, 'Could not read the paper.'));
    } finally {
      setLoading(false);
    }
  };

  const handleDraftFromKey = async (files) => {
    const file = files[0];
    if (!file) return;
    try {
      setLoading(true);
      const res = await api.draftCriteria(examId.trim(), file);
      setRubricQuestions(res.questions);
      setExtractedSummary((prev) => ({
        ...prev,
        warnings: [...(prev?.warnings || []), ...res.problems],
      }));
      setHasUnsavedEdits(true);
      setQuestionsChecked(false);
      toast.success('Marking points drafted. Please check them.');
    } catch (err) {
      toast.error(errorText(err, 'Could not draft marking points from this answer key.'));
    } finally {
      setLoading(false);
    }
  };

  // Every edit copies what it changes, so React can tell what actually moved.
  const editQuestion = (qIdx, change) => {
    setRubricQuestions((list) => list.map((q, i) => (i === qIdx ? change(q) : q)));
    setHasUnsavedEdits(true);
  };

  const handlers = {
    correctOption: (qIdx, val) => editQuestion(qIdx, (q) => ({ ...q, correct_option: val })),
    questionText: (qIdx, text) => editQuestion(qIdx, (q) => ({ ...q, question_text: text })),
    maxMarks: (qIdx, val) => editQuestion(qIdx, (q) => ({ ...q, max_marks: Number(val) })),
    criterion: (qIdx, cIdx, field, val) => editQuestion(qIdx, (q) => ({
      ...q,
      criteria: q.criteria.map((c, i) => (i === cIdx ? { ...c, [field]: field === 'max_marks' ? Number(val) : val } : c)),
    })),
    addCriterion: (qIdx) => editQuestion(qIdx, (q) => ({
      ...q,
      criteria: [...q.criteria, { description: 'Additional marking point', max_marks: 1 }],
    })),
    removeCriterion: (qIdx, cIdx) => editQuestion(qIdx, (q) => ({
      ...q,
      criteria: q.criteria.filter((_, i) => i !== cIdx),
    })),
  };

  // Step 2: Approve & Save Rubric
  const handleApproveRubric = async () => {
    if (saving || !questionsChecked) return; // prevent duplicate clicks and unconfirmed approval
    try {
      setSaving(true);

      const calculatedTotal = rubricQuestions.reduce((s, q) => s + (Number(q.max_marks) || 0), 0);
      const targetTotal = extractedSummary?.total_marks || calculatedTotal;
      // The Gemini path checks against the printed total, and works out the total itself.
      const printedTotal = extractedSummary?.source_kind === 'gemini'
        ? extractedSummary.printed_total
        : targetTotal;

      let res;
      try {
        res = await api.approveRubric(
          examId.trim(),
          rubricQuestions,
          docFingerprint,
          genFingerprint,
          printedTotal,
          questionsChecked,
          { style: markingStyle, notes: markingNotes },
        );
      } catch (err) {
        const detail = err.response?.data?.detail || '';
        const totalOnly = extractedSummary?.source_kind === 'gemini'
          && detail.includes('The paper prints a total of')
          && !detail.includes('criteria add up')
          && !detail.includes('has no marks');
        if (!totalOnly) throw err;
        const goAhead = await confirm({
          title: 'The totals do not match',
          body: detail,
          confirmLabel: 'Approve with the calculated total',
        });
        if (!goAhead) return;
        res = await api.approveRubric(
          examId.trim(),
          rubricQuestions,
          docFingerprint,
          genFingerprint,
          null,
          questionsChecked,
          { style: markingStyle, notes: markingNotes },
        );
      }

      if (res.warnings && res.warnings.length > 0) {
        console.warn('[Setup] Rubric approved with warnings:', res.warnings);
      }

      setExtractedSummary((prev) => ({
        ...prev,
        is_approved: true,
        version: res.version || 1,
        rubric_id: res.rubric_id,
        total_marks: res.total_marks || targetTotal,
      }));
      setHasUnsavedEdits(false);
      setStep(3);
      toast.success('Marks approved. Next, add the answer sheets.');

      if (onExamApproved) {
        onExamApproved(examId.trim());
      }
    } catch (err) {
      console.error('Rubric approval error:', err);
      toast.error(errorText(err, 'Could not save the approved marks.'));
    } finally {
      setSaving(false);
    }
  };

  // Reset draft to configure a fresh paper
  const handleResetDraft = async () => {
    if (hasUnsavedEdits) {
      const goAhead = await confirm({
        title: 'Start over?',
        body: 'Your changes to this draft have not been approved, and they will be lost.',
        confirmLabel: 'Start over',
        danger: true,
      });
      if (!goAhead) return;
    }
    try {
      sessionStorage.removeItem(`autograde_setup_${examId.trim()}`);
    } catch {
      // ignore
    }
    setStep(1);
    setQuestionFile(null);
    setTextbookFile(null);
    setPaperKeyFile(null);
    setRubricQuestions([]);
    setExtractedSummary(null);
    setQuestionsChecked(false);
    setGenFingerprint('');
    setDocFingerprint('');
    setHasUnsavedEdits(false);
  };

  const displayTotal = extractedSummary?.total_marks !== undefined
    ? extractedSummary.total_marks
    : rubricQuestions.reduce((s, q) => s + (Number(q.max_marks) || 0), 0);

  const validationOk = ['VALID', 'RECONCILED'].includes(extractedSummary?.validation_status);
  const toCheck = [
    ...(extractedSummary?.review_items || []).map((item) => `${item.question}: ${item.reason}`),
    ...(extractedSummary?.warnings || []),
  ];

  return (
    <div className={cx('mx-auto px-4 py-8 sm:px-6', step === 2 ? 'max-w-5xl' : 'max-w-3xl')}>
      <PageHeader
        eyebrow="Step 1 of 4"
        title={step === 2 ? 'Check the marks' : 'Set up the exam'}
        subtitle={step === 2
          ? 'AutoGrade read these from the paper. Fix anything that looks wrong: nothing is marked until you approve this.'
          : 'Add the question paper. AutoGrade reads the questions and marks, then you check them.'}
      />

      <Stepper step={step} canReview={rubricQuestions.length > 0} onStep={setStep} />

      {step === 1 && (
        <form key="step1" onSubmit={handleGenerateRubric} className="page-enter space-y-5">
          <div className="card space-y-5 p-5 sm:p-6">
            <ExamIdInput value={examId} onCommit={onExamIdChange} />

            <div>
              <div className="label">Question paper</div>
              <Dropzone
                required
                title="Drop the question paper here, or click to choose"
                hint="PDF, JPG, PNG or TXT"
                accept=".pdf,.jpg,.jpeg,.png,.txt"
                files={questionFile ? [questionFile] : []}
                onFiles={(files) => setQuestionFile(files[0] || null)}
                onReject={toast.error}
              />
            </div>

            <div>
              <div className="label">What kind of answers does this paper have?</div>
              <Segmented label="Kind of answers" value={paperType} onChange={setPaperType} options={PAPER_TYPES} />
              <p className="mt-1.5 text-sm text-muted">{PAPER_TYPE_HINT[paperType]}</p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <div className="label">Answer key</div>
                <Dropzone
                  compact
                  optional
                  title="Add an answer key"
                  hint="Helps draft the marking points"
                  accept=".pdf,.txt,.png,.jpg,.jpeg"
                  files={paperKeyFile ? [paperKeyFile] : []}
                  onFiles={(files) => setPaperKeyFile(files[0] || null)}
                  onReject={toast.error}
                />
              </div>
              <div>
                <div className="label">Reference notes</div>
                <Dropzone
                  compact
                  optional
                  title="Add notes or a textbook"
                  hint="PDF or image"
                  accept=".pdf,.jpg,.jpeg,.png"
                  files={textbookFile ? [textbookFile] : []}
                  onFiles={(files) => setTextbookFile(files[0] || null)}
                  onReject={toast.error}
                />
              </div>
            </div>
          </div>

          {loading && (
            <div className="fade rounded-xl bg-primary-soft px-4 py-3" role="status">
              <div className="flex items-center gap-2 text-sm font-semibold text-primary">
                <Sparkles size={16} aria-hidden="true" />
                Reading your paper. This usually takes a few seconds.
              </div>
              <div className="indeterminate mt-2.5 h-1.5 rounded-full bg-surface" aria-hidden="true" />
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-2">
              {rubricQuestions.length > 0 && (
                <>
                  <Button variant="secondary" onClick={() => setStep(2)}>
                    View draft ({rubricQuestions.length} questions)
                  </Button>
                  <Button variant="danger" icon={Trash2} onClick={handleResetDraft}>Clear draft</Button>
                </>
              )}
            </div>
            <Button type="submit" variant="primary" size="lg" loading={loading}>
              {loading ? 'Analyzing' : 'Analyze the paper'}
              {!loading && <ArrowRight size={17} aria-hidden="true" />}
            </Button>
          </div>
        </form>
      )}

      {step === 2 && (
        <div key="step2" className="page-enter space-y-4">
          {extractedSummary && (
            <div className="card space-y-4 p-5">
              {extractedSummary.is_approved && (
                <Callout tone="good" title={`Approved (version ${extractedSummary.version || 1})`}>
                  This exam already has approved marks. You can keep them and move on to Upload.
                </Callout>
              )}

              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <SummaryTile label="Questions"><CountUp value={rubricQuestions.length} /></SummaryTile>
                <SummaryTile label="Total marks"><CountUp value={Number(displayTotal) || 0} decimals={Number.isInteger(Number(displayTotal)) ? 0 : 1} /></SummaryTile>
                <SummaryTile label="Total printed on paper">
                  {extractedSummary.declared_maximum_marks != null
                    ? extractedSummary.declared_maximum_marks
                    : <span className="text-base text-muted">Not printed</span>}
                </SummaryTile>
                <SummaryTile label="Marks check">
                  <span className={cx('inline-flex items-center gap-1.5 text-base', validationOk ? 'text-good' : 'text-warn')}>
                    {validationOk && <CheckCircle2 size={17} aria-hidden="true" />}
                    {extractedSummary.validation_status === 'VALID'
                      ? 'Passed'
                      : extractedSummary.validation_status === 'RECONCILED'
                        ? 'Passed, with changes'
                        : (extractedSummary.validation_status || 'Passed')}
                  </span>
                </SummaryTile>
              </div>

              {toCheck.length > 0 && (
                <FoldingCallout tone="warn" title="Things to check" items={toCheck} />
              )}

              {(extractedSummary.sections?.length > 0 || extractedSummary.reconciliation_notes?.length > 0) && (
                <Disclosure title="How the total was worked out">
                  <div className="space-y-2 text-sm text-muted">
                    {extractedSummary.sections?.length > 0 && <p>Sections: {extractedSummary.sections.join(' · ')}</p>}
                    {extractedSummary.reconciliation_notes?.length > 0 && (
                      <ul className="list-disc space-y-1 pl-4">
                        {extractedSummary.reconciliation_notes.map((note, idx) => <li key={idx}>{note}</li>)}
                      </ul>
                    )}
                  </div>
                </Disclosure>
              )}
            </div>
          )}

          <div className="space-y-3">
            {rubricQuestions.map((q, qIdx) => (
              <QuestionCard key={qIdx} q={q} qIdx={qIdx} index={qIdx} handlers={handlers} />
            ))}
          </div>

          <div className="card p-4">
            <Disclosure title="More options">
              <div className="space-y-4">
                <div className="max-w-md">
                  <div className="label">Draft marking points from an answer key</div>
                  <Dropzone
                    compact
                    title="Add a CSV or text answer key"
                    hint="You can edit everything before approving"
                    accept=".txt,.csv"
                    disabled={loading}
                    onFiles={handleDraftFromKey}
                    onReject={toast.error}
                  />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button icon={RefreshCw} loading={loading} onClick={(e) => handleGenerateRubric(e, true)}>Read the paper again</Button>
                  <Button variant="danger" icon={Trash2} onClick={handleResetDraft}>Clear draft and use a different paper</Button>
                </div>
              </div>
            </Disclosure>
          </div>

          {rubricQuestions.some((q) => q.question_format !== 'MCQ') && (
            <div className="card space-y-3 p-5 sm:p-6">
              <div>
                <h3 className="text-lg">How strictly should written answers be marked?</h3>
                <p className="text-sm text-muted">Pick what is closest to how you mark. You can change it later and approve again.</p>
              </div>
              <Segmented
                label="Marking strictness"
                value={markingStyle}
                onChange={(value) => { setMarkingStyle(value); setHasUnsavedEdits(true); }}
                options={MARKING_STYLES}
              />
              <p className="text-sm text-muted">{MARKING_STYLE_HINT[markingStyle]}</p>
              <div>
                <label htmlFor="marking-notes" className="label">Your own marking rules <span className="font-normal text-muted">(optional)</span></label>
                <textarea
                  id="marking-notes"
                  className="input min-h-20"
                  maxLength={600}
                  placeholder="For example: cut half a mark for spelling mistakes in key terms. A map without labels gets half."
                  value={markingNotes}
                  onChange={(e) => { setMarkingNotes(e.target.value); setHasUnsavedEdits(true); }}
                />
              </div>
            </div>
          )}

          {/* The one thing to do on this step stays within reach while scrolling. */}
          <div className="sticky bottom-20 z-30 md:bottom-4">
            <div className="flex flex-col gap-3 rounded-2xl border border-line bg-surface/95 p-3 shadow-float backdrop-blur-md sm:flex-row sm:items-center sm:justify-between sm:pl-4">
              <label className="flex items-center gap-2.5 text-sm font-medium">
                <input type="checkbox" className="check" checked={questionsChecked} onChange={(e) => setQuestionsChecked(e.target.checked)} />
                I checked every question and mark against the paper
                {hasUnsavedEdits && <Pill tone="warn" className="fade">Not approved yet</Pill>}
              </label>
              <div className="flex shrink-0 items-center justify-end gap-2">
                <Button variant="ghost" icon={ArrowLeft} onClick={() => setStep(1)}>Back</Button>
                <Tip text={questionsChecked ? 'Lock these marks and move on' : 'Tick the box first'}>
                  <Button variant="primary" icon={ShieldCheck} loading={saving} disabled={!questionsChecked} onClick={handleApproveRubric}>
                    Approve
                  </Button>
                </Tip>
              </div>
            </div>
          </div>
        </div>
      )}

      {step === 3 && (
        <div key="step3" className="card page-enter mx-auto max-w-lg space-y-4 p-8 text-center">
          <div className="pop mx-auto grid size-16 place-items-center rounded-full bg-good-soft text-good">
            <CheckCircle2 size={32} aria-hidden="true" />
          </div>
          <h2 className="text-2xl">The marks are approved</h2>
          <p className="text-muted">
            <strong className="text-ink">{examId}</strong> has {rubricQuestions.length} questions worth {displayTotal} marks.
          </p>
          <div className="flex flex-wrap justify-center gap-2 pt-2">
            <Button icon={ArrowLeft} onClick={() => setStep(2)}>See the marks again</Button>
            <Button variant="primary" onClick={() => onExamApproved?.(examId.trim())}>
              Add answer sheets
              <ArrowRight size={16} aria-hidden="true" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
