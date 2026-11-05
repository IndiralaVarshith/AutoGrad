import React, { useState, useEffect, useCallback } from 'react';
import api from '../api';

export default function Setup({ onExamApproved, onExamIdChange, initialExamId = 'varshith' }) {
  const [examId, setExamId] = useState(initialExamId);
  const [step, setStep] = useState(1);
  const [questionFile, setQuestionFile] = useState(null);
  const [textbookFile, setTextbookFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Rubric state for Step 2
  const [rubricQuestions, setRubricQuestions] = useState([]);
  const [extractedSummary, setExtractedSummary] = useState(null);
  const [genFingerprint, setGenFingerprint] = useState('');
  const [docFingerprint, setDocFingerprint] = useState('');
  const [hasUnsavedEdits, setHasUnsavedEdits] = useState(false);
  const [questionsChecked, setQuestionsChecked] = useState(false);

  // Sync initialExamId from parent
  useEffect(() => {
    if (initialExamId && initialExamId !== examId) {
      setExamId(initialExamId);
    }
  }, [initialExamId]);

  // Load draft from sessionStorage or approved rubric from Firestore whenever examId changes
  const loadStateForExam = useCallback(async (targetExamId) => {
    if (!targetExamId || !targetExamId.trim()) return;
    const cleanId = targetExamId.trim();

    // 1. Check sessionStorage for local in-progress draft
    try {
      const rawDraft = sessionStorage.getItem(`autograde_setup_${cleanId}`);
      if (rawDraft) {
        const draft = JSON.parse(rawDraft);
        if (draft && draft.rubricQuestions && draft.rubricQuestions.length > 0) {
          setStep(draft.step || 2);
          setRubricQuestions(draft.rubricQuestions);
          setExtractedSummary(draft.extractedSummary || null);
          setGenFingerprint(draft.genFingerprint || '');
          setDocFingerprint(draft.docFingerprint || '');
          setHasUnsavedEdits(Boolean(draft.hasUnsavedEdits));
          return;
        }
      }
    } catch (e) {
      console.warn('[Setup] Failed to parse sessionStorage draft:', e);
    }

    // 2. If no local draft, check Firestore for an already-approved rubric
    try {
      const existing = await api.getExamRubric(cleanId);
      if (existing && existing.questions && existing.questions.length > 0) {
        setStep(2);
        setRubricQuestions(existing.questions);
        setExtractedSummary({
          total_marks: existing.total_marks || existing.questions.reduce((s, q) => s + (q.max_marks || 0), 0),
          declared_maximum_marks: existing.total_marks,
          calculated_maximum_marks: existing.total_marks,
          validation_status: 'VALID',
          sections: existing.sections || [],
          warnings: [],
          reconciliation_notes: ['Loaded teacher-approved rubric from Firestore.'],
          from_cache: true,
          is_approved: true,
          version: existing.version || 1,
          rubric_id: existing.rubric_id || `${cleanId}_v1`,
          questions_count: existing.questions.length,
        });
        setHasUnsavedEdits(false);
      }
    } catch (e) {
      // No existing rubric in Firestore — that is fine, user can generate
    }
  }, []);

  useEffect(() => {
    loadStateForExam(examId);
  }, [examId, loadStateForExam]);

  // Persist draft to sessionStorage whenever rubric or summary changes
  useEffect(() => {
    if (!examId.trim()) return;
    try {
      if (rubricQuestions.length > 0 || extractedSummary) {
        const draftData = {
          step,
          examId: examId.trim(),
          rubricQuestions,
          extractedSummary,
          genFingerprint,
          docFingerprint,
          hasUnsavedEdits,
        };
        sessionStorage.setItem(`autograde_setup_${examId.trim()}`, JSON.stringify(draftData));
      }
    } catch (e) {
      // ignore quota errors
    }
  }, [step, examId, rubricQuestions, extractedSummary, genFingerprint, docFingerprint, hasUnsavedEdits]);

  const handleExamIdInputChange = (newId) => {
    setExamId(newId);
    if (onExamIdChange) {
      onExamIdChange(newId);
    }
  };

  // Step 1: Upload question paper & generate proposed rubric
  const handleGenerateRubric = async (e, forceRegenerate = false) => {
    if (e && e.preventDefault) e.preventDefault();
    if (!examId.trim()) {
      setError('Please specify an Exam Identifier.');
      return;
    }
    if (!questionFile) {
      setError('Please upload a Question Paper (PDF or Image).');
      return;
    }

    try {
      setLoading(true);
      setError('');

      const formData = new FormData();
      formData.append('exam_id', examId.trim());
      formData.append('question_paper', questionFile);
      if (textbookFile) {
        formData.append('textbook', textbookFile);
      }
      formData.append('force_regenerate', forceRegenerate ? 'true' : 'false');

      const res = await api.setupExam(formData);

      const questions = res.rubric?.questions || res.questions || [];
      if (questions.length === 0) {
        setError('No questions were extracted from the question paper. Please verify the file is a valid question paper.');
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
        declared_maximum_marks: res.rubric?.declared_maximum_marks ?? totalMarks,
        calculated_maximum_marks: res.rubric?.calculated_maximum_marks ?? totalMarks,
        validation_status: res.rubric?.validation_status || 'VALID',
        reconciliation_notes: res.rubric?.reconciliation_notes || [],
        sections: res.rubric?.sections || [],
        warnings: res.rubric?.extraction_warnings || [],
        review_items: res.rubric?.review_items || [],
        from_cache: res.from_cache || false,
        questions_count: questions.length,
        schema_version: res.rubric?.schema_version || '',
        is_approved: false,
      });

      setHasUnsavedEdits(false);
      setQuestionsChecked(false);
      setStep(2);
    } catch (err) {
      console.error('Setup error:', err);
      setError(err.response?.data?.detail || err.message || 'Failed to generate rubric proposal.');
    } finally {
      setLoading(false);
    }
  };

  // Step 2: Inline rubric editing
  const handleCriterionChange = (qIdx, cIdx, field, val) => {
    const updated = [...rubricQuestions];
    updated[qIdx].criteria[cIdx][field] = field === 'max_marks' ? Number(val) : val;
    setRubricQuestions(updated);
    setHasUnsavedEdits(true);
  };

  const handleAddCriterion = (qIdx) => {
    const updated = [...rubricQuestions];
    updated[qIdx].criteria.push({
      description: 'Additional criterion',
      max_marks: 1,
    });
    setRubricQuestions(updated);
    setHasUnsavedEdits(true);
  };

  const handleRemoveCriterion = (qIdx, cIdx) => {
    const updated = [...rubricQuestions];
    updated[qIdx].criteria.splice(cIdx, 1);
    setRubricQuestions(updated);
    setHasUnsavedEdits(true);
  };

  const handleQuestionTextChange = (qIdx, text) => {
    const updated = [...rubricQuestions];
    updated[qIdx].question_text = text;
    setRubricQuestions(updated);
    setHasUnsavedEdits(true);
  };

  const handleMaxMarksChange = (qIdx, val) => {
    const updated = [...rubricQuestions];
    updated[qIdx].max_marks = Number(val);
    setRubricQuestions(updated);
    setHasUnsavedEdits(true);
  };

  // Step 2: Approve & Save Rubric to Firestore
  const handleApproveRubric = async () => {
    if (saving || !questionsChecked) return; // prevent duplicate clicks and unconfirmed approval
    try {
      setSaving(true);
      setError('');

      const targetTotal = extractedSummary?.total_marks || rubricQuestions.reduce((s, q) => s + (Number(q.max_marks) || 0), 0);

      const res = await api.approveRubric(
        examId.trim(),
        rubricQuestions,
        docFingerprint,
        genFingerprint,
        targetTotal,
        questionsChecked
      );

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

      if (onExamApproved) {
        onExamApproved(examId.trim());
      }
    } catch (err) {
      console.error('Rubric approval error:', err);
      setError(err.response?.data?.detail || err.message || 'Failed to save rubric to Firestore.');
    } finally {
      setSaving(false);
    }
  };

  // Reset draft to configure a fresh paper
  const handleResetDraft = () => {
    if (hasUnsavedEdits && !window.confirm('You have unsaved changes to this rubric draft. Are you sure you want to start over?')) {
      return;
    }
    try {
      sessionStorage.removeItem(`autograde_setup_${examId.trim()}`);
    } catch (e) {
      // ignore
    }
    setStep(1);
    setQuestionFile(null);
    setTextbookFile(null);
    setRubricQuestions([]);
    setExtractedSummary(null);
    setQuestionsChecked(false);
    setGenFingerprint('');
    setDocFingerprint('');
    setHasUnsavedEdits(false);
    setError('');
  };

  // Validated total marks from the structured extraction
  const displayTotal = extractedSummary?.total_marks !== undefined
    ? extractedSummary.total_marks
    : rubricQuestions.reduce((s, q) => s + (Number(q.max_marks) || 0), 0);

  return (
    <div className="max-w-5xl mx-auto py-6 px-4 sm:px-6">
      {/* Header */}
      <div className="border-b border-[#22242a] pb-4 mb-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-[#ededed] tracking-tight">
              Exam Setup &amp; Evaluation Rubric
            </h1>
            <p className="text-xs text-[#717680] mt-0.5 font-mono">
              Upload your question paper — AutoGrade extracts questions, attempt rules, and marks automatically
            </p>
          </div>

          <div className="flex items-center gap-1 font-mono text-[11px]">
            <button
              type="button"
              onClick={() => setStep(1)}
              className={`px-2.5 py-1 rounded border transition-colors cursor-pointer ${
                step === 1 ? 'bg-[#1e2026] text-[#ededed] border-[#30333c]' : 'bg-[#121316] text-[#555963] border-[#1f2126]'
              }`}
            >
              1. Upload
            </button>
            <span className="text-[#383c44]">/</span>
            <button
              type="button"
              disabled={rubricQuestions.length === 0}
              onClick={() => rubricQuestions.length > 0 && setStep(2)}
              className={`px-2.5 py-1 rounded border transition-colors ${
                rubricQuestions.length > 0 ? 'cursor-pointer' : 'cursor-not-allowed opacity-50'
              } ${
                step === 2 ? 'bg-[#1e2026] text-[#ededed] border-[#30333c]' : 'bg-[#121316] text-[#555963] border-[#1f2126]'
              }`}
            >
              2. Review Rubric
            </button>
            <span className="text-[#383c44]">/</span>
            <span className={`px-2.5 py-1 rounded border ${step === 3 ? 'bg-[#1e2026] text-[#ededed] border-[#30333c]' : 'bg-[#121316] text-[#555963] border-[#1f2126]'}`}>
              3. Ready
            </span>
          </div>
        </div>
      </div>

      {error && (
        <div className="mb-5 p-3 rounded bg-[#241212] border border-[#441d1d] text-[#ef4444] text-xs font-mono flex items-center justify-between">
          <span>{error}</span>
          <button type="button" onClick={() => setError('')} className="text-[#ef4444] hover:text-white ml-2 text-sm">×</button>
        </div>
      )}

      {/* STEP 1: Upload Paper */}
      {step === 1 && (
        <form onSubmit={handleGenerateRubric} className="space-y-5">
          <div className="bg-[#121316] border border-[#22242a] rounded p-5 space-y-5">
            <div className="flex items-center justify-between">
              <div className="text-xs font-mono font-medium uppercase tracking-wider text-[#8c9099]">
                Exam Configuration
              </div>
              {rubricQuestions.length > 0 && (
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  className="text-xs font-mono text-[#3b82f6] hover:underline cursor-pointer"
                >
                  View current draft ({rubricQuestions.length} questions) →
                </button>
              )}
            </div>

            {/* Exam Identifier */}
            <div className="max-w-sm">
              <label className="block text-[11px] font-mono text-[#8c9099] uppercase mb-1">
                Exam Identifier
              </label>
              <input
                type="text"
                value={examId}
                onChange={(e) => handleExamIdInputChange(e.target.value)}
                placeholder="varshith"
                className="w-full bg-[#0c0d0e] border border-[#25282f] rounded px-3 py-1.5 text-xs text-[#ededed] font-mono focus:outline-none focus:border-[#404552]"
                required
              />
              <p className="mt-1 text-[10px] text-[#555963] font-mono">
                Unique identifier for this exam (e.g. varshith, CS101_MID, EXAM_2026)
              </p>
            </div>

            {/* File Uploads */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
              <div className="border border-[#25282f] bg-[#0c0d0e] rounded p-4 text-left">
                <div className="text-xs font-medium text-[#ededed]">Question Paper <span className="text-[#ef4444]">*</span></div>
                <div className="text-[11px] text-[#555963] mt-0.5 mb-3 font-mono">
                  PDF, JPG, or PNG — questions, attempt rules, and marks are auto-extracted
                </div>
                <input
                  type="file"
                  accept=".pdf,.jpg,.jpeg,.png"
                  onChange={(e) => setQuestionFile(e.target.files[0])}
                  className="text-xs text-[#717680] file:mr-2 file:py-1 file:px-2.5 file:rounded file:border file:border-[#2e323b] file:bg-[#1a1c21] file:text-[#b0b4be] file:text-xs file:font-mono hover:file:bg-[#22252c] cursor-pointer"
                  required
                />
                {questionFile && (
                  <div className="mt-2 text-[11px] text-[#4ade80] font-mono truncate">
                    ✓ {questionFile.name} ({(questionFile.size / 1024).toFixed(0)} KB)
                  </div>
                )}
              </div>

              <div className="border border-[#25282f] bg-[#0c0d0e] rounded p-4 text-left">
                <div className="text-xs font-medium text-[#ededed]">Reference Material / Notes <span className="text-[#555963]">(Optional)</span></div>
                <div className="text-[11px] text-[#555963] mt-0.5 mb-3 font-mono">
                  PDF or image — used for grounding rubric criteria
                </div>
                <input
                  type="file"
                  accept=".pdf,.jpg,.jpeg,.png"
                  onChange={(e) => setTextbookFile(e.target.files[0])}
                  className="text-xs text-[#717680] file:mr-2 file:py-1 file:px-2.5 file:rounded file:border file:border-[#2e323b] file:bg-[#1a1c21] file:text-[#b0b4be] file:text-xs file:font-mono hover:file:bg-[#22252c] cursor-pointer"
                />
                {textbookFile && (
                  <div className="mt-2 text-[11px] text-[#93c5fd] font-mono truncate">
                    ✓ {textbookFile.name}
                  </div>
                )}
              </div>
            </div>

            {/* Info banner */}
            <div className="bg-[#0d1117] border border-[#1e2226] rounded p-3 text-[11px] text-[#717680] font-mono leading-relaxed">
              ℹ️ AutoGrade reads the question paper header (e.g. Max Marks: 20) and sections (e.g. 4x1=4M, 4x2=8M, 2x4=8M).
              Internal choice questions (OR choices) and attempt rules ("answer any 2") are calculated deterministically.
            </div>
          </div>

          <div className="flex justify-between items-center">
            {rubricQuestions.length > 0 ? (
              <button
                type="button"
                onClick={handleResetDraft}
                className="text-xs font-mono text-[#ef4444] hover:underline cursor-pointer"
              >
                🗑 Clear draft &amp; start over
              </button>
            ) : <span />}

            <button
              type="submit"
              disabled={loading}
              className="bg-[#2563eb] hover:bg-[#1d4ed8] text-white font-medium py-2 px-5 rounded text-xs transition-colors cursor-pointer disabled:opacity-50 font-mono"
            >
              {loading ? '⏳ Analyzing Question Paper & Extracting Marks...' : 'Analyze & Propose Rubric →'}
            </button>
          </div>
        </form>
      )}

      {/* STEP 2: Review & Edit Proposed Rubric */}
      {step === 2 && (
        <div className="space-y-5">
          {/* Extracted Exam Structure & Mathematical Validation Card */}
          {extractedSummary && (
            <div className="bg-[#0d1520] border border-[#1a2e44] rounded p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="text-[11px] font-mono uppercase tracking-wider text-[#3b82f6] font-semibold flex items-center gap-2">
                  <span>📋 Examination Structure &amp; Marks Validation</span>
                  {extractedSummary.is_approved && (
                    <span className="text-[10px] px-2 py-0.5 rounded bg-[#112217] text-[#4ade80] border border-[#1b3825]">
                      ✓ Approved in Firestore (v{extractedSummary.version || 1})
                    </span>
                  )}
                </div>
                {extractedSummary.from_cache && !extractedSummary.is_approved && (
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[#1a1c21] border border-[#2c2f37] text-[#717680]">
                    ↩ Cached draft
                  </span>
                )}
              </div>

              {/* Metrics Grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs font-mono">
                <div className="bg-[#121316] border border-[#22242a] rounded p-2.5">
                  <div className="text-[10px] text-[#717680] uppercase mb-0.5">Declared Paper Marks</div>
                  <div className="text-[#38bdf8] font-bold text-base">
                    {extractedSummary.declared_maximum_marks !== undefined ? `${extractedSummary.declared_maximum_marks}M` : '—'}
                  </div>
                </div>

                <div className="bg-[#121316] border border-[#22242a] rounded p-2.5">
                  <div className="text-[10px] text-[#717680] uppercase mb-0.5">Calculated Max Marks</div>
                  <div className="text-[#4ade80] font-bold text-base">
                    {displayTotal}M
                  </div>
                </div>

                <div className="bg-[#121316] border border-[#22242a] rounded p-2.5">
                  <div className="text-[10px] text-[#717680] uppercase mb-0.5">Marks Validation</div>
                  <div className={`font-semibold text-sm ${
                    extractedSummary.validation_status === 'VALID' || extractedSummary.validation_status === 'RECONCILED'
                      ? 'text-[#4ade80]'
                      : 'text-[#f59e0b]'
                  }`}>
                    {extractedSummary.validation_status === 'VALID' ? '✓ Passed' : (extractedSummary.validation_status || 'Passed')}
                  </div>
                </div>

                <div className="bg-[#121316] border border-[#22242a] rounded p-2.5">
                  <div className="text-[10px] text-[#717680] uppercase mb-0.5">Questions Extracted</div>
                  <div className="text-[#ededed] font-semibold text-sm">
                    {rubricQuestions.length} questions
                  </div>
                </div>
              </div>

              {/* Sections Breakdown */}
              {extractedSummary.sections && extractedSummary.sections.length > 0 && (
                <div className="text-[11px] font-mono text-[#8c9099] pt-1">
                  <span className="text-[#717680]">Detected Sections:</span>{' '}
                  <span className="text-[#ededed]">{extractedSummary.sections.join(' · ')}</span>
                </div>
              )}

              {/* Reconciliation Notes */}
              {extractedSummary.reconciliation_notes && extractedSummary.reconciliation_notes.length > 0 && (
                <div className="bg-[#12161f] border border-[#1e2533] rounded p-2.5 space-y-1">
                  <div className="text-[10px] font-mono text-[#717680] uppercase">Arithmetic &amp; Choice Reconciliation:</div>
                  {extractedSummary.reconciliation_notes.map((note, idx) => (
                    <div key={idx} className="text-[11px] font-mono text-[#38bdf8] flex gap-1.5 items-start">
                      <span>•</span>
                      <span>{note}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Validation Warnings */}
              {extractedSummary.warnings && extractedSummary.warnings.length > 0 && (
                <div className="mt-2 space-y-1">
                  {extractedSummary.warnings.map((w, i) => (
                    <div key={i} className="text-[10px] font-mono text-[#f59e0b] flex gap-1 items-start">
                      <span>⚠️</span>
                      <span>{w}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* Questions the teacher should check first */}
              {extractedSummary.review_items && extractedSummary.review_items.length > 0 && (
                <div className="mt-2 space-y-1">
                  <div className="text-[10px] font-mono uppercase text-[#f59e0b]">Check these first</div>
                  {extractedSummary.review_items.map((item, i) => (
                    <div key={i} className="text-[10px] font-mono text-[#f59e0b] flex gap-1 items-start">
                      <span>{item.question}:</span>
                      <span>{item.reason}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Teacher confirmation */}
          <label className="flex items-center gap-2 bg-[#121316] p-3 rounded border border-[#22242a] text-xs text-[#ededed] cursor-pointer">
            <input
              type="checkbox"
              checked={questionsChecked}
              onChange={(e) => setQuestionsChecked(e.target.checked)}
            />
            <span>I checked every question and mark against the paper</span>
          </label>

          {/* Action Bar */}
          <div className="flex items-center justify-between bg-[#121316] p-3.5 rounded border border-[#22242a]">
            <div>
              <div className="text-[10px] font-mono uppercase text-[#717680]">Exam: {examId}</div>
              <div className="text-xs font-semibold text-[#ededed] flex items-center gap-2">
                <span>Review &amp; Edit Marking Rubric ({rubricQuestions.length} questions, {displayTotal} marks)</span>
                {hasUnsavedEdits && (
                  <span className="text-[10px] text-[#f59e0b] font-mono bg-[#2a1e0d] px-1.5 py-0.5 rounded border border-[#4d3814]">
                    ● Unsaved edits
                  </span>
                )}
              </div>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setStep(1)}
                className="px-3 py-1.5 rounded text-xs font-mono text-[#8c9099] hover:bg-[#181a1d] border border-[#25282f] transition-colors cursor-pointer"
              >
                ← Back
              </button>
              <button
                type="button"
                onClick={(e) => handleGenerateRubric(e, true)}
                disabled={loading}
                className="px-3 py-1.5 rounded text-xs font-mono text-[#8c9099] hover:bg-[#181a1d] border border-[#25282f] transition-colors disabled:opacity-50 cursor-pointer"
                title="Regenerate rubric from question paper"
              >
                {loading ? 'Regenerating...' : '↺ Regenerate'}
              </button>
              <button
                type="button"
                onClick={handleApproveRubric}
                disabled={saving || !questionsChecked}
                className="bg-[#15803d] hover:bg-[#166534] text-white font-medium px-4 py-1.5 rounded text-xs font-mono transition-colors cursor-pointer disabled:opacity-50 flex items-center gap-1.5"
              >
                {saving ? 'Saving to Firestore...' : '✓ Approve & Lock Rubric'}
              </button>
            </div>
          </div>

          {/* Question List */}
          <div className="space-y-4">
            {rubricQuestions.map((q, qIdx) => {
              const criteriaSum = q.criteria.reduce((s, c) => s + (Number(c.max_marks) || 0), 0);
              const marksMatch = Math.abs(criteriaSum - Number(q.max_marks)) <= 0.05;
              return (
                <div key={qIdx} className="bg-[#121316] border border-[#22242a] rounded p-4 space-y-3">
                  <div className="flex items-start justify-between gap-3 border-b border-[#1c1e23] pb-2.5">
                    <div className="flex items-center gap-2 flex-1">
                      <span className="font-mono text-xs font-semibold text-[#8c9099] shrink-0">
                        Q{q.question_number}
                        {q.sub_part ? <span className="text-[#38bdf8]">({q.sub_part})</span> : null}
                        {q.section ? <span className="ml-1 text-[#555963]">[{q.section}]</span> : null}
                        {q.choice_group ? (
                          <span className="ml-1 text-[10px] bg-[#1e2533] text-[#38bdf8] px-1.5 py-0.5 rounded font-mono">
                            OR Choice
                          </span>
                        ) : null}
                      </span>
                      <input
                        type="text"
                        value={q.question_text}
                        onChange={(e) => handleQuestionTextChange(qIdx, e.target.value)}
                        className="bg-transparent border-b border-transparent hover:border-[#2c2f37] focus:border-[#404552] text-xs text-[#ededed] focus:outline-none px-1 py-0.5 flex-1"
                      />
                    </div>
                    <div className="flex items-center gap-1.5 font-mono text-xs shrink-0">
                      <span className="text-[#717680] text-[11px]">Marks:</span>
                      <input
                        type="number"
                        step="0.5"
                        value={q.max_marks}
                        onChange={(e) => handleMaxMarksChange(qIdx, e.target.value)}
                        className="w-14 bg-[#0c0d0e] border border-[#25282f] rounded px-1.5 py-0.5 text-xs text-[#ededed] text-center focus:outline-none"
                      />
                      {!marksMatch && (
                        <span className="text-[#f59e0b] text-[10px]" title={`Criteria sum (${criteriaSum}) ≠ question marks (${q.max_marks})`}>⚠</span>
                      )}
                    </div>
                  </div>

                  {/* Criteria List */}
                  <div className="space-y-1.5">
                    {q.criteria.map((c, cIdx) => (
                      <div key={cIdx} className="flex items-center gap-2 bg-[#0c0d0e] p-2 rounded border border-[#1f2126]">
                        <input
                          type="text"
                          value={c.description}
                          onChange={(e) => handleCriterionChange(qIdx, cIdx, 'description', e.target.value)}
                          className="flex-1 bg-transparent text-xs text-[#c4c7cf] focus:outline-none"
                        />
                        <div className="flex items-center gap-1 font-mono shrink-0">
                          <span className="text-[10px] text-[#555963]">Criterion Marks:</span>
                          <input
                            type="number"
                            step="0.5"
                            value={c.max_marks}
                            onChange={(e) => handleCriterionChange(qIdx, cIdx, 'max_marks', e.target.value)}
                            className="w-12 bg-[#121316] border border-[#22242a] rounded px-1 py-0.5 text-xs text-[#ededed] text-center focus:outline-none"
                          />
                        </div>
                        <button
                          type="button"
                          onClick={() => handleRemoveCriterion(qIdx, cIdx)}
                          className="text-[#555963] hover:text-[#ef4444] px-1 text-xs cursor-pointer"
                        >
                          ×
                        </button>
                      </div>
                    ))}

                    <div className="flex items-center justify-between pt-1">
                      <button
                        type="button"
                        onClick={() => handleAddCriterion(qIdx)}
                        className="text-[11px] text-[#717680] hover:text-[#c4c7cf] font-mono cursor-pointer"
                      >
                        + Add Criterion
                      </button>
                      <span className={`text-[10px] font-mono ${marksMatch ? 'text-[#4ade80]' : 'text-[#f59e0b]'}`}>
                        Criteria Sum: {criteriaSum.toFixed(1)} / {Number(q.max_marks).toFixed(1)}
                        {marksMatch ? ' ✓' : ' ⚠ criteria sum mismatch'}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Bottom approve button */}
          <div className="flex justify-between items-center pt-2">
            <button
              type="button"
              onClick={handleResetDraft}
              className="text-xs font-mono text-[#ef4444] hover:underline cursor-pointer"
            >
              🗑 Clear draft &amp; upload different paper
            </button>

            <button
              type="button"
              onClick={handleApproveRubric}
              disabled={saving}
              className="bg-[#15803d] hover:bg-[#166534] text-white font-medium px-5 py-2 rounded text-xs font-mono transition-colors cursor-pointer disabled:opacity-50"
            >
              {saving ? 'Saving to Firestore...' : '✓ Approve & Lock Rubric'}
            </button>
          </div>
        </div>
      )}

      {/* STEP 3: Rubric Approved Confirmation */}
      {step === 3 && (
        <div className="bg-[#121316] border border-[#1f3825] rounded p-6 text-center max-w-lg mx-auto space-y-4">
          <div className="text-sm font-semibold text-[#4ade80] font-mono uppercase tracking-wider">
            ✓ Rubric Approved &amp; Verified in Firestore
          </div>
          <p className="text-xs text-[#8c9099] leading-relaxed">
            Marking rubric for exam <span className="font-mono text-[#ededed]">{examId}</span> has been
            saved and verified in Firestore.
          </p>
          <div className="text-xs text-[#717680] font-mono bg-[#0c0d0e] border border-[#1f2126] p-3 rounded">
            <div>Total Questions: {rubricQuestions.length}</div>
            <div className="text-[#4ade80] font-bold text-sm mt-1">Maximum Examination Marks: {displayTotal}M</div>
          </div>

          <div className="pt-2 flex justify-center gap-3">
            <button
              type="button"
              onClick={() => setStep(2)}
              className="px-3 py-1.5 rounded text-xs font-mono text-[#8c9099] hover:bg-[#181a1d] border border-[#25282f] transition-colors cursor-pointer"
            >
              ← Review Rubric Again
            </button>

            <button
              type="button"
              onClick={() => onExamApproved && onExamApproved(examId.trim())}
              className="bg-[#2563eb] hover:bg-[#1d4ed8] text-white font-medium py-2 px-5 rounded text-xs font-mono transition-colors cursor-pointer"
            >
              Proceed to Upload Answer Sheets →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
