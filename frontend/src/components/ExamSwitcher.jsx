import React, { useEffect, useRef, useState } from 'react';
import { BookOpen, Check, ChevronsUpDown, Plus, Trash2 } from 'lucide-react';
import api from '../api';
import { Tip, cx, errorText } from './ui';
import { useFeedback } from './feedback';

/** The exam the whole app is working on. Pick one, type a new ID, or delete an exam. */
export default function ExamSwitcher({ value, onChange, refreshKey, onExamDeleted }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [exams, setExams] = useState([]);
  const [deleting, setDeleting] = useState('');
  const root = useRef(null);
  const input = useRef(null);
  const { toast, confirm } = useFeedback();

  useEffect(() => setDraft(value), [value]);

  const loadExams = () => api.getExams()
    .then((res) => { if (res?.exams) setExams(res.exams); })
    .catch(() => {});

  useEffect(() => {
    loadExams();
  }, [refreshKey]);

  useEffect(() => {
    if (!open) return undefined;
    loadExams();
    input.current?.select();
    // While the delete question is open, a click on it must not close this menu.
    const onPointer = (e) => {
      if (!root.current?.contains(e.target) && !document.querySelector('dialog[open]')) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape' && !document.querySelector('dialog[open]')) setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const choose = (examId) => {
    onChange(examId);
    setOpen(false);
  };

  const remove = async (exam) => {
    const goAhead = await confirm({
      title: `Delete exam “${exam.exam_id}”?`,
      body: 'This deletes its question paper, approved marks, calibration and every answer sheet with its marks and pages. It cannot be undone.',
      confirmLabel: 'Delete exam',
      danger: true,
    });
    if (!goAhead) return;
    try {
      setDeleting(exam.exam_id);
      const res = await api.deleteExam(exam.exam_id);
      setExams((list) => list.filter((item) => item.exam_id !== exam.exam_id));
      toast.success(`Deleted ${exam.exam_id} and ${res.sheets_deleted} ${res.sheets_deleted === 1 ? 'sheet' : 'sheets'}.`);
      onExamDeleted?.(exam.exam_id);
    } catch (err) {
      toast.error(errorText(err, 'Could not delete the exam.'));
    } finally {
      setDeleting('');
    }
  };

  const query = draft.trim().toLowerCase();
  const shown = query && query !== value.toLowerCase()
    ? exams.filter((exam) => exam.exam_id.toLowerCase().includes(query))
    : exams;
  const isNew = draft.trim() && !exams.some((exam) => exam.exam_id === draft.trim());

  return (
    <div ref={root} className="relative min-w-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="exam-tab flex max-w-48 items-center gap-2 py-1.5 pr-2 pl-2.5 text-left sm:max-w-64"
      >
        <BookOpen size={16} className="shrink-0 text-primary" aria-hidden="true" />
        <span className="min-w-0 leading-tight">
          <span className="block text-[11px] font-medium text-muted">Exam</span>
          <span className={cx('block truncate text-sm font-semibold', !value && 'text-muted')}>{value || 'Choose an exam'}</span>
        </span>
        <ChevronsUpDown size={15} className="shrink-0 text-muted" aria-hidden="true" />
      </button>

      {open && (
        <div role="dialog" aria-label="Choose an exam" className="sheet toast-in absolute top-full left-0 z-50 mt-2 w-[min(22rem,calc(100vw-2rem))] p-2 shadow-float">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.trim()) choose(draft.trim());
            }}
          >
            <input
              ref={input}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Find an exam or type a new ID"
              aria-label="Exam ID"
              className="input"
            />
          </form>

          <ul className="mt-2 max-h-72 overflow-y-auto">
            {isNew && (
              <li>
                <button
                  type="button"
                  onClick={() => choose(draft.trim())}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm font-semibold text-primary transition-colors hover:bg-primary-soft"
                >
                  <Plus size={15} aria-hidden="true" />
                  Start a new exam “{draft.trim()}”
                </button>
              </li>
            )}
            {shown.map((exam) => (
              <li key={exam.exam_id} className="group flex items-center gap-1 rounded-lg transition-colors hover:bg-sunken">
                <button
                  type="button"
                  onClick={() => choose(exam.exam_id)}
                  className="flex min-w-0 flex-1 items-center justify-between gap-3 px-2.5 py-2 text-left text-sm"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-semibold">{exam.exam_id}</span>
                    <span className="block truncate text-xs text-muted">
                      {exam.paper_filename || (exam.question_count ? `${exam.question_count} questions` : 'No question paper yet')}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1.5 text-[13px] text-muted tabular-nums">
                    {exam.total_marks ? `${exam.total_marks} marks` : ''}
                    {exam.exam_id === value && <Check size={15} className="text-primary" aria-label="Selected" />}
                  </span>
                </button>
                <Tip text="Delete this exam" className="tip-end">
                  <button
                    type="button"
                    onClick={() => remove(exam)}
                    disabled={deleting === exam.exam_id}
                    aria-label={`Delete exam ${exam.exam_id}`}
                    className="icon-btn icon-btn-danger mr-1 size-8 opacity-60 group-hover:opacity-100 focus-visible:opacity-100"
                  >
                    <Trash2 size={15} aria-hidden="true" />
                  </button>
                </Tip>
              </li>
            ))}
            {shown.length === 0 && !isNew && (
              <li className="px-2.5 py-3 text-sm text-muted">No exams yet. Type an ID to start one.</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
