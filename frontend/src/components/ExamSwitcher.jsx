import React, { useEffect, useRef, useState } from 'react';
import { Check, ChevronsUpDown } from 'lucide-react';
import api from '../api';
import { cx } from './ui';

/** The exam the whole app is working on. Pick an existing one, or type a new ID. */
export default function ExamSwitcher({ value, onChange, refreshKey }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [exams, setExams] = useState([]);
  const root = useRef(null);
  const input = useRef(null);

  useEffect(() => setDraft(value), [value]);

  useEffect(() => {
    let stopped = false;
    api.getExams()
      .then((res) => { if (!stopped && res?.exams) setExams(res.exams); })
      .catch(() => {});
    return () => {
      stopped = true;
    };
  }, [refreshKey]);

  useEffect(() => {
    if (!open) return undefined;
    input.current?.select();
    const onPointer = (e) => {
      if (!root.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const choose = (id) => {
    onChange(id);
    setOpen(false);
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
        className="flex max-w-44 items-center gap-2 rounded-[0.65rem] border border-line bg-surface py-1.5 pr-2 pl-3 text-left transition-colors hover:border-line-strong sm:max-w-60"
      >
        <span className="min-w-0 leading-tight">
          <span className="block text-[11px] font-medium text-muted">Exam ID</span>
          <span className={cx('block truncate text-sm font-semibold', !value && 'text-muted')}>{value || 'Choose an exam'}</span>
        </span>
        <ChevronsUpDown size={15} className="shrink-0 text-muted" aria-hidden="true" />
      </button>

      {open && (
        <div role="dialog" aria-label="Choose an exam" className="toast-in absolute top-full left-0 z-50 mt-2 w-[min(20rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-2 shadow-float">
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
              placeholder="Find or type a new exam ID"
              aria-label="Exam ID"
              className="input"
            />
          </form>

          <ul className="mt-2 max-h-64 overflow-y-auto">
            {shown.map((exam) => (
              <li key={exam.exam_id}>
                <button
                  type="button"
                  onClick={() => choose(exam.exam_id)}
                  className="flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left text-sm transition-colors hover:bg-sunken"
                >
                  <span className="truncate font-medium">{exam.exam_id}</span>
                  <span className="flex shrink-0 items-center gap-1.5 text-[13px] text-muted tabular-nums">
                    {exam.total_marks ? `${exam.total_marks} marks` : ''}
                    {exam.exam_id === value && <Check size={15} className="text-primary" aria-label="Selected" />}
                  </span>
                </button>
              </li>
            ))}
            {isNew && (
              <li>
                <button
                  type="button"
                  onClick={() => choose(draft.trim())}
                  className="w-full rounded-lg px-2.5 py-2 text-left text-sm font-medium text-primary transition-colors hover:bg-primary-soft"
                >
                  Use new exam “{draft.trim()}”
                </button>
              </li>
            )}
            {shown.length === 0 && !isNew && (
              <li className="px-2.5 py-3 text-sm text-muted">No exams yet. Type an ID to start one.</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
