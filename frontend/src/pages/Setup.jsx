import React, { useState } from 'react';
import api from '../api';

export default function Setup({ onExamApproved, initialExamId = 'EXAM_2026_01' }) {
  const [step, setStep] = useState(1);
  const [examId, setExamId] = useState(initialExamId);
  const [questionFile, setQuestionFile] = useState(null);
  const [textbookFile, setTextbookFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Rubric state for Step 2
  const [rubricQuestions, setRubricQuestions] = useState([]);
  const [extractedSummary, setExtractedSummary] = useState(null); // {total_marks, sections, warnings, from_cache}
  const [genFingerprint, setGenFingerprint] = useState('');
  const [docFingerprint, setDocFingerprint] = useState('');

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

      setRubricQuestions(questions);
      setGenFingerprint(res.gen_fingerprint || '');
      setDocFingerprint(res.doc_fingerprint || '');
      setExtractedSummary({
        total_marks: res.rubric?.total_marks || questions.reduce((s, q) => s + (q.max_marks || 0), 0),
        sections: res.rubric?.sections || [],
        warnings: res.rubric?.extraction_warnings || [],
        from_cache: res.from_cache || false,
        questions_count: questions.length,
        schema_version: res.rubric?.schema_version || '',
      });

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
  };

  const handleAddCriterion = (qIdx) => {
    const updated = [...rubricQuestions];
    updated[qIdx].criteria.push({
      description: 'Additional criterion',
      max_marks: 1,
    });
    setRubricQuestions(updated);
  };

  const handleRemoveCriterion = (qIdx, cIdx) => {
    const updated = [...rubricQuestions];
    updated[qIdx].criteria.splice(cIdx, 1);
    setRubricQuestions(updated);
  };

  const handleQuestionTextChange = (qIdx, text) => {
    const updated = [...rubricQuestions];
    updated[qIdx].question_text = text;
    setRubricQuestions(updated);
  };

  const handleMaxMarksChange = (qIdx, val) => {
    const updated = [...rubricQuestions];
    updated[qIdx].max_marks = Number(val);
    setRubricQuestions(updated);
  };

  // Step 2: Approve & Save Rubric to Firestore
  const handleApproveRubric = async () => {
    try {
      setLoading(true);
      setError('');
      const res = await api.approveRubric(examId, rubricQuestions, docFingerprint, genFingerprint);
      if (res.warnings && res.warnings.length > 0) {
        console.warn('[Setup] Rubric approved with warnings:', res.warnings);
      }
      setStep(3);
    } catch (err) {
      console.error('Rubric approval error:', err);
      setError(err.response?.data?.detail || err.message || 'Failed to save rubric to Firestore.');
    } finally {
      setLoading(false);
    }
  };

  // Computed totals for review display
  const reviewTotal = rubricQuestions.reduce((s, q) => s + (Number(q.max_marks) || 0), 0);

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
              Upload your question paper — AutoGrade extracts questions and marks automatically
            </p>
          </div>

          <div className="flex items-center gap-1 font-mono text-[11px]">
            <span className={`px-2.5 py-1 rounded border ${step === 1 ? 'bg-[#1e2026] text-[#ededed] border-[#30333c]' : 'bg-[#121316] text-[#555963] border-[#1f2126]'}`}>
              1. Upload
            </span>
            <span className="text-[#383c44]">/</span>
            <span className={`px-2.5 py-1 rounded border ${step === 2 ? 'bg-[#1e2026] text-[#ededed] border-[#30333c]' : 'bg-[#121316] text-[#555963] border-[#1f2126]'}`}>
              2. Review Rubric
            </span>
            <span className="text-[#383c44]">/</span>
            <span className={`px-2.5 py-1 rounded border ${step === 3 ? 'bg-[#1e2026] text-[#ededed] border-[#30333c]' : 'bg-[#121316] text-[#555963] border-[#1f2126]'}`}>
              3. Ready
            </span>
          </div>
        </div>
      </div>

      {error && (
        <div className="mb-5 p-3 rounded bg-[#241212] border border-[#441d1d] text-[#ef4444] text-xs font-mono">
          {error}
        </div>
      )}

      {/* STEP 1: Upload Paper */}
      {step === 1 && (
        <form onSubmit={handleGenerateRubric} className="space-y-5">
          <div className="bg-[#121316] border border-[#22242a] rounded p-5 space-y-5">
            <div className="text-xs font-mono font-medium uppercase tracking-wider text-[#8c9099]">
              Exam Configuration
            </div>

            {/* Only Exam Identifier — no manual question count or marks */}
            <div className="max-w-sm">
              <label className="block text-[11px] font-mono text-[#8c9099] uppercase mb-1">
                Exam Identifier
              </label>
              <input
                type="text"
                value={examId}
                onChange={(e) => setExamId(e.target.value)}
                placeholder="CS101_MIDTERM_2026"
                className="w-full bg-[#0c0d0e] border border-[#25282f] rounded px-3 py-1.5 text-xs text-[#ededed] font-mono focus:outline-none focus:border-[#404552]"
                required
              />
              <p className="mt-1 text-[10px] text-[#555963] font-mono">
                A short unique identifier for this exam (e.g. CS101_MID, varshith, EXAM_2026)
              </p>
            </div>

            {/* File Uploads */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
              <div className="border border-[#25282f] bg-[#0c0d0e] rounded p-4 text-left">
                <div className="text-xs font-medium text-[#ededed]">Question Paper <span className="text-[#ef4444]">*</span></div>
                <div className="text-[11px] text-[#555963] mt-0.5 mb-3 font-mono">
                  PDF, JPG, or PNG — questions and marks extracted automatically
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
            <div className="bg-[#0d1117] border border-[#1e2226] rounded p-3 text-[11px] text-[#717680] font-mono">
              ℹ️ AutoGrade will read the question paper and automatically extract: question text, marks per question, sections, subquestions, and internal choices.
              You do not need to enter total questions or marks manually.
            </div>
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={loading}
              className="bg-[#2563eb] hover:bg-[#1d4ed8] text-white font-medium py-2 px-5 rounded text-xs transition-colors cursor-pointer disabled:opacity-50 font-mono"
            >
              {loading ? '⏳ Analyzing Question Paper...' : 'Analyze & Propose Rubric →'}
            </button>
          </div>
        </form>
      )}

      {/* STEP 2: Review & Edit Proposed Rubric */}
      {step === 2 && (
        <div className="space-y-5">
          {/* Extracted Exam Summary Banner */}
          {extractedSummary && (
            <div className="bg-[#0d1520] border border-[#1a2e44] rounded p-4 space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-[11px] font-mono uppercase tracking-wider text-[#3b82f6]">
                  📋 Automatically Extracted Examination Structure
                </div>
                {extractedSummary.from_cache && (
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[#1a1c21] border border-[#2c2f37] text-[#717680]">
                    ↩ Cached (same paper detected)
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs font-mono">
                <div className="bg-[#121316] border border-[#22242a] rounded p-2.5">
                  <div className="text-[10px] text-[#717680] uppercase mb-0.5">Questions</div>
                  <div className="text-[#ededed] font-semibold text-sm">{extractedSummary.questions_count}</div>
                </div>
                <div className="bg-[#121316] border border-[#22242a] rounded p-2.5">
                  <div className="text-[10px] text-[#717680] uppercase mb-0.5">Total Marks</div>
                  <div className="text-[#4ade80] font-semibold text-sm">{extractedSummary.total_marks}</div>
                </div>
                <div className="bg-[#121316] border border-[#22242a] rounded p-2.5">
                  <div className="text-[10px] text-[#717680] uppercase mb-0.5">Sections</div>
                  <div className="text-[#ededed] font-semibold text-sm">
                    {extractedSummary.sections.length > 0 ? extractedSummary.sections.join(', ') : '—'}
                  </div>
                </div>
                <div className="bg-[#121316] border border-[#22242a] rounded p-2.5">
                  <div className="text-[10px] text-[#717680] uppercase mb-0.5">Current Total</div>
                  <div className={`font-semibold text-sm ${Math.abs(reviewTotal - extractedSummary.total_marks) > 0.1 ? 'text-[#f59e0b]' : 'text-[#ededed]'}`}>
                    {reviewTotal}
                  </div>
                </div>
              </div>
              {extractedSummary.warnings && extractedSummary.warnings.length > 0 && (
                <div className="mt-2 space-y-1">
                  {extractedSummary.warnings.map((w, i) => (
                    <div key={i} className="text-[10px] font-mono text-[#f59e0b] flex gap-1">
                      <span>⚠️</span><span>{w}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="flex items-center justify-between bg-[#121316] p-3.5 rounded border border-[#22242a]">
            <div>
              <div className="text-[10px] font-mono uppercase text-[#717680]">Exam: {examId}</div>
              <div className="text-xs font-semibold text-[#ededed]">
                Review &amp; Edit Marking Rubric ({rubricQuestions.length} questions, {reviewTotal} marks)
              </div>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setStep(1)}
                className="px-3 py-1.5 rounded text-xs font-mono text-[#8c9099] hover:bg-[#181a1d] border border-[#25282f] transition-colors"
              >
                ← Back
              </button>
              <button
                type="button"
                onClick={(e) => handleGenerateRubric(e, true)}
                disabled={loading}
                className="px-3 py-1.5 rounded text-xs font-mono text-[#8c9099] hover:bg-[#181a1d] border border-[#25282f] transition-colors disabled:opacity-50"
                title="Generate a new rubric proposal (ignores cached version)"
              >
                {loading ? 'Regenerating...' : '↺ Regenerate'}
              </button>
              <button
                type="button"
                onClick={handleApproveRubric}
                disabled={loading}
                className="bg-[#15803d] hover:bg-[#166534] text-white font-medium px-4 py-1.5 rounded text-xs font-mono transition-colors cursor-pointer disabled:opacity-50"
              >
                {loading ? 'Saving to Firestore...' : '✓ Approve & Lock Rubric'}
              </button>
            </div>
          </div>

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
                        {q.section ? <span className="ml-1 text-[#555963]">[{q.section}]</span> : null}
                      </span>
                      <input
                        type="text"
                        value={q.question_text}
                        onChange={(e) => handleQuestionTextChange(qIdx, e.target.value)}
                        className="bg-transparent border-b border-transparent hover:border-[#2c2f37] focus:border-[#404552] text-xs text-[#ededed] focus:outline-none px-1 py-0.5 flex-1"
                      />
                    </div>
                    <div className="flex items-center gap-1.5 font-mono text-xs shrink-0">
                      <span className="text-[#717680] text-[11px]">Max:</span>
                      <input
                        type="number"
                        step="0.5"
                        value={q.max_marks}
                        onChange={(e) => handleMaxMarksChange(qIdx, e.target.value)}
                        className="w-14 bg-[#0c0d0e] border border-[#25282f] rounded px-1.5 py-0.5 text-xs text-[#ededed] text-center focus:outline-none"
                      />
                      {!marksMatch && (
                        <span className="text-[#f59e0b] text-[10px]" title={`Criteria sum: ${criteriaSum}`}>⚠</span>
                      )}
                    </div>
                  </div>

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
                          <span className="text-[10px] text-[#555963]">Marks:</span>
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
                          className="text-[#555963] hover:text-[#ef4444] px-1 text-xs"
                        >
                          ×
                        </button>
                      </div>
                    ))}

                    <div className="flex items-center justify-between pt-1">
                      <button
                        type="button"
                        onClick={() => handleAddCriterion(qIdx)}
                        className="text-[11px] text-[#717680] hover:text-[#c4c7cf] font-mono"
                      >
                        + Add Criterion
                      </button>
                      <span className={`text-[10px] font-mono ${marksMatch ? 'text-[#4ade80]' : 'text-[#f59e0b]'}`}>
                        Sum: {criteriaSum.toFixed(1)} / {Number(q.max_marks).toFixed(1)}
                        {marksMatch ? ' ✓' : ' ⚠ mismatch'}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Bottom approve button for long rubrics */}
          <div className="flex justify-end pt-2">
            <button
              type="button"
              onClick={handleApproveRubric}
              disabled={loading}
              className="bg-[#15803d] hover:bg-[#166534] text-white font-medium px-5 py-2 rounded text-xs font-mono transition-colors cursor-pointer disabled:opacity-50"
            >
              {loading ? 'Saving to Firestore...' : '✓ Approve & Lock Rubric'}
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
            saved and verified in Firestore. You can now upload student answer sheets for evaluation.
          </p>
          <div className="text-xs text-[#717680] font-mono">
            {rubricQuestions.length} questions · {reviewTotal} total marks
          </div>

          <div className="pt-2">
            <button
              onClick={() => onExamApproved && onExamApproved(examId)}
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
