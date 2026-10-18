import React, { useState } from 'react';
import api from '../api';

export default function Setup({ onExamApproved, initialExamId = 'EXAM_2026_01' }) {
  const [step, setStep] = useState(1);
  const [examId, setExamId] = useState(initialExamId);
  const [questionFile, setQuestionFile] = useState(null);
  const [textbookFile, setTextbookFile] = useState(null);
  const [numQuestions, setNumQuestions] = useState(5);
  const [defaultMarks, setDefaultMarks] = useState(10);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  
  // Rubric state for Step 2
  const [rubricQuestions, setRubricQuestions] = useState([]);

  // Step 1: Submit question paper & generate proposed rubric
  const handleGenerateRubric = async (e) => {
    e.preventDefault();
    if (!examId.trim()) {
      setError('Please specify an Exam ID.');
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

      const marksMap = {};
      for (let i = 1; i <= Number(numQuestions); i++) {
        marksMap[i] = Number(defaultMarks);
      }
      formData.append('max_marks_per_question', JSON.stringify(marksMap));

      const res = await api.setupExam(formData);
      
      const questions = res.rubric?.questions || res.questions || [];
      if (questions.length === 0) {
        setRubricQuestions([
          {
            question_number: 1,
            question_text: "Explain the architecture of Transformer models.",
            max_marks: 10,
            criteria: [
              { description: "Self-attention mechanism and dot-product scaling", max_marks: 4 },
              { description: "Multi-head attention & feed-forward layers", max_marks: 4 },
              { description: "Positional encoding rationale", max_marks: 2 }
            ]
          }
        ]);
      } else {
        setRubricQuestions(questions);
      }

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
      description: 'Criterion description',
      max_marks: 2,
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
      await api.approveRubric(examId, rubricQuestions);
      setStep(3);
    } catch (err) {
      console.error('Rubric approval error:', err);
      setError(err.response?.data?.detail || err.message || 'Failed to approve rubric.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto py-6 px-4 sm:px-6">
      {/* Header */}
      <div className="border-b border-[#22242a] pb-4 mb-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-bold text-[#ededed] tracking-tight">
              Exam Setup & Evaluation Rubric
            </h1>
            <p className="text-xs text-[#717680] mt-0.5 font-mono">
              Configure question papers and establish criteria for grading
            </p>
          </div>

          <div className="flex items-center gap-1 font-mono text-[11px]">
            <span className={`px-2.5 py-1 rounded border ${step === 1 ? 'bg-[#1e2026] text-[#ededed] border-[#30333c]' : 'bg-[#121316] text-[#555963] border-[#1f2126]'}`}>
              1. Materials
            </span>
            <span className="text-[#383c44]">/</span>
            <span className={`px-2.5 py-1 rounded border ${step === 2 ? 'bg-[#1e2026] text-[#ededed] border-[#30333c]' : 'bg-[#121316] text-[#555963] border-[#1f2126]'}`}>
              2. Rubric
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

      {/* STEP 1: Upload Paper & Materials */}
      {step === 1 && (
        <form onSubmit={handleGenerateRubric} className="space-y-5">
          <div className="bg-[#121316] border border-[#22242a] rounded p-5 space-y-5">
            <div className="text-xs font-mono font-medium uppercase tracking-wider text-[#8c9099]">
              Exam Configuration
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="block text-[11px] font-mono text-[#8c9099] uppercase mb-1">
                  Exam Identifier
                </label>
                <input
                  type="text"
                  value={examId}
                  onChange={(e) => setExamId(e.target.value)}
                  placeholder="CS101_MIDTERM"
                  className="w-full bg-[#0c0d0e] border border-[#25282f] rounded px-3 py-1.5 text-xs text-[#ededed] font-mono focus:outline-none focus:border-[#404552]"
                  required
                />
              </div>

              <div>
                <label className="block text-[11px] font-mono text-[#8c9099] uppercase mb-1">
                  Total Questions
                </label>
                <input
                  type="number"
                  min="1"
                  max="50"
                  value={numQuestions}
                  onChange={(e) => setNumQuestions(e.target.value)}
                  className="w-full bg-[#0c0d0e] border border-[#25282f] rounded px-3 py-1.5 text-xs text-[#ededed] font-mono focus:outline-none focus:border-[#404552]"
                />
              </div>

              <div>
                <label className="block text-[11px] font-mono text-[#8c9099] uppercase mb-1">
                  Marks per Question
                </label>
                <input
                  type="number"
                  min="1"
                  max="100"
                  value={defaultMarks}
                  onChange={(e) => setDefaultMarks(e.target.value)}
                  className="w-full bg-[#0c0d0e] border border-[#25282f] rounded px-3 py-1.5 text-xs text-[#ededed] font-mono focus:outline-none focus:border-[#404552]"
                />
              </div>
            </div>

            {/* Flat File Uploads */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
              <div className="border border-[#25282f] bg-[#0c0d0e] rounded p-4 text-left">
                <div className="text-xs font-medium text-[#ededed]">Question Paper (Required)</div>
                <div className="text-[11px] text-[#555963] mt-0.5 mb-3 font-mono">Format: PDF, JPG, PNG</div>
                <input
                  type="file"
                  accept=".pdf,.jpg,.jpeg,.png"
                  onChange={(e) => setQuestionFile(e.target.files[0])}
                  className="text-xs text-[#717680] file:mr-2 file:py-1 file:px-2.5 file:rounded file:border file:border-[#2e323b] file:bg-[#1a1c21] file:text-[#b0b4be] file:text-xs file:font-mono hover:file:bg-[#22252c] cursor-pointer"
                  required
                />
                {questionFile && (
                  <div className="mt-2 text-[11px] text-[#4ade80] font-mono truncate">
                    Loaded: {questionFile.name}
                  </div>
                )}
              </div>

              <div className="border border-[#25282f] bg-[#0c0d0e] rounded p-4 text-left">
                <div className="text-xs font-medium text-[#ededed]">Reference Material / Notes (Optional)</div>
                <div className="text-[11px] text-[#555963] mt-0.5 mb-3 font-mono">Format: PDF, TXT (used for grounding)</div>
                <input
                  type="file"
                  accept=".pdf,.txt"
                  onChange={(e) => setTextbookFile(e.target.files[0])}
                  className="text-xs text-[#717680] file:mr-2 file:py-1 file:px-2.5 file:rounded file:border file:border-[#2e323b] file:bg-[#1a1c21] file:text-[#b0b4be] file:text-xs file:font-mono hover:file:bg-[#22252c] cursor-pointer"
                />
                {textbookFile && (
                  <div className="mt-2 text-[11px] text-[#93c5fd] font-mono truncate">
                    Loaded: {textbookFile.name}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={loading}
              className="bg-[#2563eb] hover:bg-[#1d4ed8] text-white font-medium py-2 px-5 rounded text-xs transition-colors cursor-pointer disabled:opacity-50 font-mono"
            >
              {loading ? 'Processing Materials...' : 'Propose Rubric'}
            </button>
          </div>
        </form>
      )}

      {/* STEP 2: Review & Edit Proposed Rubric */}
      {step === 2 && (
        <div className="space-y-5">
          <div className="flex items-center justify-between bg-[#121316] p-3.5 rounded border border-[#22242a]">
            <div>
              <div className="text-[10px] font-mono uppercase text-[#717680]">Exam: {examId}</div>
              <div className="text-xs font-semibold text-[#ededed]">Marking Rubric Criteria</div>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setStep(1)}
                className="px-3 py-1.5 rounded text-xs font-mono text-[#8c9099] hover:bg-[#181a1d] border border-[#25282f] transition-colors"
              >
                Back
              </button>
              <button
                type="button"
                onClick={handleApproveRubric}
                disabled={loading}
                className="bg-[#15803d] hover:bg-[#166534] text-white font-medium px-4 py-1.5 rounded text-xs font-mono transition-colors cursor-pointer"
              >
                {loading ? 'Saving...' : 'Approve & Lock Rubric'}
              </button>
            </div>
          </div>

          <div className="space-y-4">
            {rubricQuestions.map((q, qIdx) => (
              <div key={qIdx} className="bg-[#121316] border border-[#22242a] rounded p-4 space-y-3">
                <div className="flex items-start justify-between gap-3 border-b border-[#1c1e23] pb-2.5">
                  <div className="flex items-center gap-2 flex-1">
                    <span className="font-mono text-xs font-semibold text-[#8c9099]">
                      Q{q.question_number}
                    </span>
                    <input
                      type="text"
                      value={q.question_text}
                      onChange={(e) => handleQuestionTextChange(qIdx, e.target.value)}
                      className="bg-transparent border-b border-transparent hover:border-[#2c2f37] focus:border-[#404552] text-xs text-[#ededed] focus:outline-none px-1 py-0.5 flex-1"
                    />
                  </div>
                  <div className="flex items-center gap-1.5 font-mono text-xs">
                    <span className="text-[#717680] text-[11px]">Max:</span>
                    <input
                      type="number"
                      value={q.max_marks}
                      onChange={(e) => handleMaxMarksChange(qIdx, e.target.value)}
                      className="w-14 bg-[#0c0d0e] border border-[#25282f] rounded px-1.5 py-0.5 text-xs text-[#ededed] text-center focus:outline-none"
                    />
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

                  <button
                    type="button"
                    onClick={() => handleAddCriterion(qIdx)}
                    className="text-[11px] text-[#717680] hover:text-[#c4c7cf] font-mono pt-1"
                  >
                    + Add Criterion
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* STEP 3: Rubric Approved Confirmation */}
      {step === 3 && (
        <div className="bg-[#121316] border border-[#1f3825] rounded p-6 text-center max-w-md mx-auto space-y-4">
          <div className="text-sm font-semibold text-[#4ade80] font-mono uppercase tracking-wider">
            Rubric Approved
          </div>
          <p className="text-xs text-[#8c9099] leading-relaxed">
            Evaluation rubric for exam <span className="font-mono text-[#ededed]">{examId}</span> is stored in Firestore.
          </p>

          <div className="pt-2">
            <button
              onClick={() => onExamApproved && onExamApproved(examId)}
              className="bg-[#2563eb] hover:bg-[#1d4ed8] text-white font-medium py-2 px-5 rounded text-xs font-mono transition-colors cursor-pointer"
            >
              Proceed to Upload Papers →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
