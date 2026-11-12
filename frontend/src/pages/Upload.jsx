import React, { useState, useEffect } from 'react';
import { ArrowRight, Check, CheckCircle2, ChevronDown, FileText, GraduationCap, Layers, Loader2, RotateCcw, Sparkles, X, Zap } from 'lucide-react';
import api from '../api';
import QueueStatus from '../components/QueueStatus';
import ConfidenceBadge from '../components/ConfidenceBadge';
import EvidenceTable from '../components/EvidenceTable';
import {
  Button, Callout, CountUp, Dropzone, Expand, PageHeader, Pill, ProgressBar, Skeleton, cx, errorText, formatBytes,
} from '../components/ui';
import { useFeedback } from '../components/feedback';

// The stages the server reports for one sheet: one Gemini request reads and marks it.
const evaluationStages = [
  { id: 'uploading', label: 'Uploading the sheet' },
  { id: 'saving', label: 'Keeping a copy of the pages' },
  { id: 'gemini_sheet', label: 'AutoGrade reads and marks every answer' },
  { id: 'completed', label: 'Checked, added up and saved' },
];

const terminalJobStatuses = new Set(['COMPLETE', 'NEEDS_REVIEW', 'FAILED', 'TIMED_OUT', 'CANCELLED']);

const evaluationStageIndex = (stage) => {
  if (['extracting_pages', 'firebase_storage', 'submission_record'].includes(stage)) return 1;
  if (stage === 'gemini_sheet') return 2;
  if (stage === 'completed') return 3;
  return 0;
};

function QuestionReport({ q }) {
  const [open, setOpen] = useState(false);
  const flagged = q.routing !== 'AUTO_ACCEPT';
  return (
    <div className="lazy-card overflow-hidden rounded-xl border border-line">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-sunken"
      >
        <ChevronDown size={16} aria-hidden="true" className={cx('text-muted transition-transform duration-300', open ? 'rotate-0' : '-rotate-90')} />
        <span className="font-semibold">Q{q.question_number}{q.sub_part ? `(${q.sub_part})` : ''}</span>
        {q.question_type && <Pill>{q.question_type}</Pill>}
        {flagged
          ? <ConfidenceBadge routing={q.routing === 'MANUAL' ? 'MANUAL' : 'FLAGGED'} score={q.c_final} />
          : <ConfidenceBadge routing="AUTO_ACCEPT" score={q.c_final} />}
        <span className="ml-auto text-sm font-semibold tabular-nums">{q.final_score} of {q.max_marks}</span>
      </button>
      <Expand open={open}>
        <div className="space-y-3 border-t border-line p-4">
          <div>
            <div className="mb-1 text-[13px] font-semibold text-muted">What the student wrote, as read by AutoGrade</div>
            <p className="rounded-lg bg-sunken p-3 text-sm whitespace-pre-wrap">{q.extracted_text || 'No answer text found for this question.'}</p>
          </div>
          {q.criteria?.length > 0 && <EvidenceTable criteria={q.criteria} />}
          <p className="text-[13px] text-muted tabular-nums">
            AutoGrade's confidence {Math.round((q.c_final || 0) * 100)}%
            {q.model_used ? ` · ${q.model_used}` : ''}
          </p>
        </div>
      </Expand>
    </div>
  );
}

export default function Upload({ examId = '', onGoToDashboard, onGoToReview, onGoToSetup, active = true }) {
  const selectedExamId = examId;
  const [files, setFiles] = useState([]);
  const [studentMeta, setStudentMeta] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [evaluatingSingle, setEvaluatingSingle] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  const [error, setError] = useState('');
  const [queue, setQueue] = useState(null);
  const { toast } = useFeedback();

  // Approved rubric state
  const [rubricInfo, setRubricInfo] = useState(null);
  const [rubricLoading, setRubricLoading] = useState(false);
  const [calibration, setCalibration] = useState(null);

  // Single student immediate grading result
  const [singleResult, setSingleResult] = useState(null);
  const [evaluationJobId, setEvaluationJobId] = useState('');
  const [evaluationJob, setEvaluationJob] = useState(null);
  const [uploadTransferPercent, setUploadTransferPercent] = useState(null);
  const [pollGeneration, setPollGeneration] = useState(0);

  const refreshQueue = async () => {
    const id = selectedExamId.trim();
    if (!id) return;
    try {
      setQueue(await api.getQueueStatus(id));
    } catch {
      // The next poll tries again.
    }
  };

  // Live queue progress. It survives a page reload or a backend restart.
  useEffect(() => {
    const id = selectedExamId.trim();
    if (!id) {
      setQueue(null);
      return undefined;
    }
    if (!active) return undefined;
    let stopped = false;
    const refresh = async () => {
      try {
        const data = await api.getQueueStatus(id);
        if (!stopped) setQueue(data);
      } catch {
        // The next poll tries again.
      }
    };
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [selectedExamId, active]);

  // Whether new sheets will be marked to the teacher's calibration sheet.
  useEffect(() => {
    const id = selectedExamId.trim();
    setCalibration(null);
    if (!id || !active) return undefined;
    let stopped = false;
    api.getCalibration(id)
      .then((data) => { if (!stopped) setCalibration(data); })
      .catch(() => {});
    return () => {
      stopped = true;
    };
  }, [selectedExamId, active]);

  // Check for an approved rubric whenever the exam changes.
  useEffect(() => {
    if (!selectedExamId.trim()) {
      setRubricInfo(null);
      return undefined;
    }

    let isMounted = true;
    const fetchRubric = async () => {
      try {
        setRubricLoading(true);
        const data = await api.getExamRubric(selectedExamId.trim());
        if (isMounted) {
          setRubricInfo(data);
          setError('');
        }
      } catch (err) {
        if (isMounted) {
          setRubricInfo(null);
          // "None yet" has its own friendly banner. Only a real failure is an error.
          setError(err.response?.status === 404 ? '' : errorText(err, 'Could not check for approved marks.'));
        }
      } finally {
        if (isMounted) setRubricLoading(false);
      }
    };

    fetchRubric();
    return () => {
      isMounted = false;
    };
  }, [selectedExamId, active]);

  useEffect(() => {
    if (!evaluationJobId) return undefined;
    let polling = true;
    let timer;

    const poll = async () => {
      try {
        const job = await api.getEvaluationJob(evaluationJobId);
        if (!polling) return;
        setEvaluationJob(job);

        if (terminalJobStatuses.has(job.status)) {
          setEvaluatingSingle(false);
          if (job.status === 'COMPLETE' || job.status === 'NEEDS_REVIEW') {
            if (!job.result) {
              setStatusMsg('');
              setError(job.error || job.stage_detail || 'A teacher needs to check this sheet. No marks were given, because no model could finish the work.');
              return;
            }
            setSingleResult(job.result);
            setStatusMsg(
              job.status === 'NEEDS_REVIEW'
                ? `Draft marks: ${job.score} of ${job.max_score}. ${job.result.flagged_count > 0 ? `${job.result.flagged_count} ${job.result.flagged_count === 1 ? 'answer needs' : 'answers need'} a teacher's check; the rest are marked.` : 'A teacher needs to check this sheet.'}`
                : `Draft marks: ${job.score} of ${job.max_score}.`,
            );
            setError('');
          } else {
            setError(
              job.error
                ? `Evaluation ${job.status.toLowerCase().replace('_', ' ')}: ${job.error}`
                : `Evaluation ${job.status.toLowerCase().replace('_', ' ')}.`,
            );
            setStatusMsg('');
          }
          return;
        }

        timer = setTimeout(poll, 1200);
      } catch (pollError) {
        if (!polling) return;
        setEvaluatingSingle(false);
        setError(errorText(pollError, 'Could not get the progress of this sheet.'));
      }
    };

    poll();
    return () => {
      polling = false;
      clearTimeout(timer);
    };
  }, [evaluationJobId, pollGeneration]);

  const handleFileSelection = (fileList) => {
    if (fileList.length === 0) return;

    setFiles(fileList);
    setSingleResult(null);
    setEvaluationJobId('');
    setEvaluationJob(null);
    setUploadTransferPercent(null);
    setStatusMsg('');

    // The whole file name: splitting it would give Student_4 and Student_5 the same ticket.
    setStudentMeta(fileList.map((f) => ({
      hall_ticket: f.name.replace(/\.[^/.]+$/, '').trim(),
      name: '',
      filename: f.name,
    })));
  };

  const handleMetaChange = (index, field, value) => {
    setStudentMeta((list) => list.map((m, i) => (i === index ? { ...m, [field]: value } : m)));
  };

  const handleRemoveFile = (index) => {
    const updatedFiles = files.filter((_, i) => i !== index);
    setFiles(updatedFiles);
    setStudentMeta(studentMeta.filter((_, i) => i !== index));
    if (updatedFiles.length === 0) setSingleResult(null);
  };

  // Submit one student and poll the persistent background evaluation job.
  const handleSingleStudentEvaluation = async () => {
    if (files.length === 0 || !selectedExamId.trim() || !rubricInfo) return;

    let accepted = false;
    try {
      setEvaluatingSingle(true);
      setError('');
      setStatusMsg('');
      setEvaluationJob(null);
      setEvaluationJobId('');
      setUploadTransferPercent(0);

      const targetFile = files[0];
      const meta = studentMeta[0] || { hall_ticket: 'STUDENT_001', name: 'Student 1' };

      const formData = new FormData();
      formData.append('exam_id', selectedExamId.trim());
      formData.append('hall_ticket', meta.hall_ticket.trim());
      formData.append('student_name', meta.name.trim());
      formData.append('file', targetFile);

      const res = await api.evaluateSingleStudent(formData, (event) => {
        if (event.total) {
          setUploadTransferPercent(Math.round((event.loaded / event.total) * 100));
        }
      });
      if (!res.job_id) {
        throw new Error('The server accepted the upload but returned no job to track.');
      }
      accepted = true;
      setEvaluationJobId(res.job_id);
      setEvaluationJob(res);
      setPollGeneration((value) => value + 1);
    } catch (err) {
      console.error('Single evaluation error:', err);
      setError(errorText(err, 'Evaluation failed.'));
    } finally {
      if (!accepted) setEvaluatingSingle(false);
    }
  };

  const handleRetryEvaluation = async () => {
    if (!evaluationJobId) return;
    try {
      setError('');
      setStatusMsg('');
      setSingleResult(null);
      const job = await api.retryEvaluationJob(evaluationJobId);
      setEvaluationJob(job);
      setEvaluatingSingle(true);
      setPollGeneration((value) => value + 1);
    } catch (err) {
      setError(errorText(err, 'Could not retry the evaluation.'));
    }
  };

  const handleCancelEvaluation = async () => {
    if (!evaluationJobId) return;
    try {
      const job = await api.cancelEvaluationJob(evaluationJobId);
      setEvaluationJob((current) => ({ ...current, ...job }));
      toast.info('Cancelling. It stops at the next safe point.');
    } catch (err) {
      setError(errorText(err, 'Could not cancel the evaluation.'));
    }
  };

  // Bulk or Queue Upload
  const handleUpload = async (e) => {
    e.preventDefault();
    if (files.length === 0 || !selectedExamId.trim()) return;

    try {
      setUploading(true);
      setError('');
      setStatusMsg('');

      const formData = new FormData();
      formData.append('exam_id', selectedExamId.trim());

      files.forEach((f) => {
        formData.append('files', f);
      });

      formData.append('student_meta', JSON.stringify(studentMeta.map((m) => ({
        hall_ticket: m.hall_ticket,
        name: m.name,
      }))));

      const res = await api.uploadAnswerSheets(formData);

      toast.success(res.message || `Queued ${res.queued ?? files.length} answer sheets.`);
      // Cleared so the same sheets cannot be sent twice by a second press.
      setFiles([]);
      setStudentMeta([]);
      setQueue(await api.getQueueStatus(selectedExamId.trim()).catch(() => queue));
    } catch (err) {
      console.error('Upload error:', err);
      setError(errorText(err, 'The upload failed.'));
    } finally {
      setUploading(false);
    }
  };

  const activeIndex = evaluationStageIndex(evaluationJob?.current_stage || (evaluationJobId ? 'queued' : 'uploading'));
  const totalSize = files.reduce((sum, file) => sum + file.size, 0);

  return (
    <div className="mx-auto max-w-4xl space-y-5 px-4 py-8 sm:px-6">
      <PageHeader
        eyebrow="Step 2 of 4"
        title="Add the answer sheets"
        subtitle="Drop in the students' handwritten sheets. AutoGrade reads them one at a time and drafts marks for you to check."
        actions={onGoToDashboard && (
          <Button variant="ghost" onClick={onGoToDashboard}>
            See results
            <ArrowRight size={16} aria-hidden="true" />
          </Button>
        )}
      />

      {error && <Callout tone="bad" onDismiss={() => setError('')}>{error}</Callout>}
      {statusMsg && <Callout tone="good" onDismiss={() => setStatusMsg('')}>{statusMsg}</Callout>}

      {!selectedExamId.trim() ? (
        <Callout tone="info" title="Choose an exam first" action={<Button size="sm" onClick={onGoToSetup}>Go to Setup</Button>}>
          Use the Exam ID menu at the top of the page, or set up a new exam.
        </Callout>
      ) : rubricLoading && !rubricInfo ? (
        <Skeleton className="h-16 rounded-xl" />
      ) : rubricInfo ? (
        <div className="fade flex flex-wrap items-center gap-3 rounded-xl bg-good-soft px-4 py-3 text-sm">
          <CheckCircle2 size={18} className="shrink-0 text-good" aria-hidden="true" />
          <span>
            <strong>{selectedExamId}</strong> is ready: {rubricInfo.questions?.length || 0} questions, {rubricInfo.total_marks || 0} marks approved.
          </span>
        </div>
      ) : (
        <Callout
          tone="warn"
          title={`No approved marks for “${selectedExamId}” yet`}
          action={<Button size="sm" onClick={onGoToSetup}>Go to Setup</Button>}
        >
          Sheets cannot be marked until a teacher approves the marks.
        </Callout>
      )}

      {rubricInfo && (calibration?.active && calibration.current ? (
        <div className="fade flex flex-wrap items-center gap-3 rounded-xl bg-primary-soft px-4 py-3 text-sm">
          <GraduationCap size={18} className="shrink-0 text-primary" aria-hidden="true" />
          <span>
            New sheets are marked to your calibration from <strong>{calibration.hall_ticket}</strong>
            {` (${calibration.corrections} ${calibration.corrections === 1 ? 'mark' : 'marks'} you changed${calibration.remarks ? `, ${calibration.remarks} with your notes` : ''}).`}
          </span>
        </div>
      ) : (
        <Callout tone="info" title="Optional: calibrate with one sheet first">
          Mark one sheet with “Mark this one now”, check it in Review, write a note on any mark you change,
          then choose “Use this sheet to calibrate”. Sheets you add after that are marked the way you marked it.
        </Callout>
      ))}

      {queue?.total_jobs > 0 && (
        <QueueStatus
          examId={selectedExamId.trim()}
          queue={queue}
          onChanged={refreshQueue}
          onGoToReview={onGoToReview}
          onResume={async () => {
            try {
              setQueue(await api.resumeQueue(selectedExamId.trim()));
            } catch (err) {
              toast.error(errorText(err, 'Could not resume the queue.'));
            }
          }}
        />
      )}

      {(evaluatingSingle || evaluationJob) && (
        <section className="card rise space-y-4 p-5" aria-live="polite" aria-label="Sheet progress">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-lg">Marking one sheet</h2>
              <p className="mt-0.5 text-sm text-muted">
                {evaluationJob?.stage_detail
                  || (evaluationJobId ? 'Waiting for news from the server' : `Uploading: ${uploadTransferPercent ?? 0}%`)}
              </p>
            </div>
            {evaluationJobId && evaluationJob && !terminalJobStatuses.has(evaluationJob.status) && (
              <Button variant="ghost" size="sm" onClick={handleCancelEvaluation}>Cancel</Button>
            )}
          </div>

          {!evaluationJobId && <ProgressBar value={uploadTransferPercent ?? 0} label="Upload progress" />}

          <ol className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
            {evaluationStages.map((stage, index) => {
              const isPending = evaluationJob?.status === 'PENDING';
              const isActive = !isPending && index === activeIndex && (evaluatingSingle || !evaluationJobId);
              const isDone = (evaluationJobId && index === 0) || index < activeIndex || (
                index === evaluationStages.length - 1
                && (evaluationJob?.status === 'COMPLETE'
                  || (evaluationJob?.status === 'NEEDS_REVIEW' && evaluationJob?.result))
              );
              return (
                <li key={stage.id} className={cx('flex items-center gap-2.5 text-sm transition-colors duration-300', isActive ? 'font-semibold text-ink' : isDone ? 'text-ink' : 'text-muted')}>
                  <span
                    className={cx(
                      'grid size-5 shrink-0 place-items-center rounded-full transition-colors duration-300',
                      isDone ? 'bg-primary text-on-primary' : isActive ? 'bg-primary-soft text-primary' : 'bg-sunken',
                    )}
                    aria-hidden="true"
                  >
                    {isDone ? <Check size={12} strokeWidth={3} className="pop" /> : isActive ? <Loader2 size={12} className="spin" /> : null}
                  </span>
                  {stage.label}
                  {isDone && <span className="sr-only">(done)</span>}
                  {isActive && <span className="sr-only">(in progress)</span>}
                </li>
              );
            })}
          </ol>

          {evaluationJob && ['FAILED', 'TIMED_OUT'].includes(evaluationJob.status) && (
            <Button variant="primary" icon={RotateCcw} onClick={handleRetryEvaluation}>Try this sheet again</Button>
          )}
        </section>
      )}

      <div className="card space-y-4 p-5 sm:p-6">
        <Dropzone
          multiple
          title="Drop answer sheets here, or click to choose"
          hint="PDF, JPG or PNG. You can add many at once."
          accept=".pdf,.jpg,.jpeg,.png"
          disabled={evaluatingSingle}
          files={files}
          onFiles={handleFileSelection}
          onReject={toast.error}
        />

        {files.length > 0 && (
          <div className="rise space-y-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-lg">
                {files.length} {files.length === 1 ? 'sheet' : 'sheets'}
                <span className="ml-2 font-sans text-sm font-normal text-muted">{formatBytes(totalSize)}</span>
              </h2>
              <p className="text-[13px] text-muted">Check each roll number. The student's name is optional.</p>
            </div>

            <ul className="divide-y divide-line rounded-xl border border-line">
              {studentMeta.map((m, idx) => (
                <li key={m.filename} className="rise grid gap-2 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-center" style={{ '--i': Math.min(idx, 8) }}>
                  <span className="flex min-w-0 items-center gap-2 text-sm">
                    <FileText size={16} className="shrink-0 text-muted" aria-hidden="true" />
                    <span className="truncate" title={m.filename}>{m.filename}</span>
                  </span>
                  <input
                    value={m.hall_ticket}
                    disabled={evaluatingSingle}
                    aria-label={`Roll number for ${m.filename}`}
                    placeholder="Roll number"
                    onChange={(e) => handleMetaChange(idx, 'hall_ticket', e.target.value)}
                    className="input min-h-9 py-1.5 text-sm"
                  />
                  <input
                    value={m.name}
                    disabled={evaluatingSingle}
                    aria-label={`Student name for ${m.filename}`}
                    placeholder="Student name (optional)"
                    onChange={(e) => handleMetaChange(idx, 'name', e.target.value)}
                    className="input min-h-9 py-1.5 text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => handleRemoveFile(idx)}
                    disabled={evaluatingSingle}
                    aria-label={`Remove ${m.filename}`}
                    className="icon-btn justify-self-end hover:text-bad!"
                  >
                    <X size={16} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>

            <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
              {files.length === 1 && (
                <Button icon={Zap} loading={evaluatingSingle} disabled={!rubricInfo} onClick={handleSingleStudentEvaluation}>
                  {evaluatingSingle ? 'Marking' : 'Mark this one now'}
                </Button>
              )}
              <Button variant="primary" size="lg" icon={Layers} loading={uploading} disabled={!rubricInfo || evaluatingSingle} onClick={handleUpload}>
                {uploading ? 'Sending' : `Submit to Queue (${files.length})`}
              </Button>
            </div>
          </div>
        )}
      </div>

      {singleResult && (
        <section className="card rise space-y-5 p-5 sm:p-6" aria-label="Draft marks for this sheet">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-[13px] font-semibold text-primary">
                <Sparkles size={14} aria-hidden="true" />
                Draft marks, not final
              </div>
              <h2 className="mt-1 text-xl">{singleResult.student_name || singleResult.hall_ticket}</h2>
              {singleResult.student_name && <p className="text-sm text-muted">{singleResult.hall_ticket}</p>}
              {singleResult.page_readers?.length > 0 && (
                <p className="mt-1 text-[13px] text-muted">
                  Read by: {singleResult.page_readers.map((r) => `page ${r.page} ${r.model}${r.status === 'READ' ? '' : ` (${r.status})`}`).join(', ')}
                </p>
              )}
            </div>
            <div className="text-right">
              <div className="font-display text-4xl font-semibold">
                <CountUp value={Number(singleResult.total_score) || 0} decimals={Number.isInteger(Number(singleResult.total_score)) ? 0 : 1} />
                <span className="ml-1 font-sans text-base font-normal text-muted">of {singleResult.max_total}</span>
              </div>
              <div className="mt-1">
                <ConfidenceBadge
                  routing={singleResult.status === 'NEEDS_REVIEW' || singleResult.flagged_count > 0 ? 'FLAGGED' : 'AUTO_ACCEPT'}
                  score={singleResult.avg_confidence}
                />
              </div>
            </div>
          </div>

          <div className="space-y-2">
            {singleResult.questions?.map((q, idx) => <QuestionReport key={idx} q={q} />)}
          </div>

          <div className="flex justify-end">
            <Button variant="primary" onClick={onGoToReview}>
              Go to Review
              <ArrowRight size={16} aria-hidden="true" />
            </Button>
          </div>
        </section>
      )}
    </div>
  );
}
