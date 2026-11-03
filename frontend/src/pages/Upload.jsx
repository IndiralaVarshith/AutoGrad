import React, { useState, useEffect } from 'react';
import api from '../api';
import QueueStatus from '../components/QueueStatus';
import ConfidenceBadge from '../components/ConfidenceBadge';
import EvidenceTable from '../components/EvidenceTable';

export default function Upload({ examId = 'EXAM_2026_01', onGoToDashboard }) {
  const [selectedExamId, setSelectedExamId] = useState(examId);
  const [files, setFiles] = useState([]);
  const [studentMeta, setStudentMeta] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [evaluatingSingle, setEvaluatingSingle] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  const [error, setError] = useState('');
  const [queueStats, setQueueStats] = useState({ total: 0, processing: 0, completed: 0, needsReview: 0 });

  // Approved rubric state from Firestore
  const [rubricInfo, setRubricInfo] = useState(null);
  const [rubricLoading, setRubricLoading] = useState(false);

  // Single student immediate grading result
  const [singleResult, setSingleResult] = useState(null);

  useEffect(() => {
    if (examId) setSelectedExamId(examId);
  }, [examId]);

  // Check approved rubric status in Firestore whenever exam ID changes
  useEffect(() => {
    if (!selectedExamId.trim()) {
      setRubricInfo(null);
      return;
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
        }
      } finally {
        if (isMounted) setRubricLoading(false);
      }
    };

    fetchRubric();
    return () => {
      isMounted = false;
    };
  }, [selectedExamId]);

  const handleFileSelection = (e) => {
    const fileList = Array.from(e.target.files);
    if (fileList.length === 0) return;

    setFiles(fileList);
    setSingleResult(null);

    const generatedMeta = fileList.map((f, idx) => {
      const base = f.name.replace(/\.[^/.]+$/, '');
      const parts = base.split(/[-_]/);
      const hallTicket = parts[0]?.trim() || `HT_${String(idx + 1).padStart(3, '0')}`;
      const name = parts[1]?.trim() || `Student ${idx + 1}`;
      return { hall_ticket: hallTicket, name: name, filename: f.name };
    });

    setStudentMeta(generatedMeta);
  };

  const handleMetaChange = (index, field, value) => {
    const updated = [...studentMeta];
    updated[index][field] = value;
    setStudentMeta(updated);
  };

  const handleRemoveFile = (index) => {
    const updatedFiles = files.filter((_, i) => i !== index);
    const updatedMeta = studentMeta.filter((_, i) => i !== index);
    setFiles(updatedFiles);
    setStudentMeta(updatedMeta);
    if (updatedFiles.length === 0) setSingleResult(null);
  };

  // Direct Synchronous Evaluation for ONE Student
  const handleSingleStudentEvaluation = async () => {
    if (files.length === 0) {
      setError('Please select one student answer sheet file.');
      return;
    }
    if (!selectedExamId.trim()) {
      setError('Please provide an Exam ID.');
      return;
    }
    if (!rubricInfo) {
      setError(`Cannot evaluate: No approved rubric exists in Firestore for exam '${selectedExamId}'. Please approve a rubric first.`);
      return;
    }

    try {
      setEvaluatingSingle(true);
      setError('');
      setStatusMsg('Evaluating handwritten answer sheet with local vision model & approved rubric...');

      const targetFile = files[0];
      const meta = studentMeta[0] || { hall_ticket: 'STUDENT_001', name: 'Student 1' };

      const formData = new FormData();
      formData.append('exam_id', selectedExamId.trim());
      formData.append('hall_ticket', meta.hall_ticket.trim());
      formData.append('student_name', meta.name.trim());
      formData.append('file', targetFile);

      const res = await api.evaluateSingleStudent(formData);
      setSingleResult(res.result);
      setStatusMsg(`Evaluation complete! Result persisted to Firestore for ${res.hall_ticket}.`);
    } catch (err) {
      console.error('Single evaluation error:', err);
      setError(err.response?.data?.detail || err.message || 'Evaluation failed.');
    } finally {
      setEvaluatingSingle(false);
    }
  };

  // Bulk or Queue Upload
  const handleUpload = async (e) => {
    e.preventDefault();
    if (files.length === 0) {
      setError('Please select at least one answer sheet file.');
      return;
    }
    if (!selectedExamId.trim()) {
      setError('Please provide an Exam ID.');
      return;
    }

    try {
      setUploading(true);
      setError('');
      setStatusMsg('');

      const formData = new FormData();
      formData.append('exam_id', selectedExamId.trim());

      files.forEach((f) => {
        formData.append('files', f);
      });

      const metaPayload = studentMeta.map((m) => ({
        hall_ticket: m.hall_ticket,
        name: m.name,
      }));
      formData.append('student_meta', JSON.stringify(metaPayload));

      const res = await api.uploadAnswerSheets(formData);

      setStatusMsg(`Dispatched ${res.queued ?? files.length} answer sheets to background queue.`);
      
      setQueueStats({
        total: files.length,
        processing: files.length,
        completed: 0,
        needsReview: 0,
      });

    } catch (err) {
      console.error('Upload error:', err);
      setError(err.response?.data?.detail || err.message || 'Bulk upload failed.');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto py-6 px-4 sm:px-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#22242a] pb-4">
        <div>
          <h1 className="text-lg font-bold text-[#ededed] tracking-tight">
            Answer Sheet Ingestion & Evaluation
          </h1>
          <p className="text-xs text-[#717680] mt-0.5 font-mono">
            Evaluate student handwritten sheets against the teacher-approved Firestore rubric
          </p>
        </div>

        {onGoToDashboard && (
          <button
            onClick={onGoToDashboard}
            className="px-3 py-1.5 rounded text-xs font-mono bg-[#16181d] hover:bg-[#202227] text-[#c4c7cf] border border-[#26282f] transition-colors"
          >
            View Dashboard →
          </button>
        )}
      </div>

      {error && (
        <div className="p-3 rounded bg-[#241212] border border-[#441d1d] text-[#ef4444] text-xs font-mono">
          {error}
        </div>
      )}

      {statusMsg && (
        <div className="p-3 rounded bg-[#112217] border border-[#1b3825] text-[#4ade80] text-xs font-mono">
          {statusMsg}
        </div>
      )}

      {/* Queue Status Widget */}
      {queueStats.total > 0 && (
        <QueueStatus stats={queueStats} />
      )}

      {/* Upload Form */}
      <div className="space-y-5">
        <div className="bg-[#121316] border border-[#22242a] rounded p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div className="text-xs font-mono uppercase tracking-wider text-[#8c9099] font-medium">
              Target Examination
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-mono text-[#717680]">Exam ID:</span>
              <input
                type="text"
                value={selectedExamId}
                onChange={(e) => setSelectedExamId(e.target.value)}
                className="bg-[#0c0d0e] border border-[#25282f] rounded px-2.5 py-1 text-xs text-[#ededed] font-mono focus:outline-none focus:border-[#404552]"
                required
              />
            </div>
          </div>

          {/* Firestore Rubric Verification Banner */}
          <div className="p-3 rounded border text-xs font-mono">
            {rubricLoading ? (
              <span className="text-[#8c9099]">Checking Firestore for approved rubric...</span>
            ) : rubricInfo ? (
              <div className="text-[#4ade80] flex items-center justify-between">
                <span>
                  ✓ Approved Rubric (Version {rubricInfo.version || 1}) loaded from Firestore: {rubricInfo.questions?.length || 0} questions, {rubricInfo.total_marks || 0} marks.
                </span>
                <span className="text-[10px] text-[#717680]">Ready for evaluation</span>
              </div>
            ) : (
              <div className="text-[#f59e0b]">
                ⚠️ No approved rubric found in Firestore for exam '{selectedExamId}'. Go to the "Setup Exam" tab to generate and approve a rubric first.
              </div>
            )}
          </div>

          {/* Flat Drop Area */}
          <div className="border border-[#25282f] bg-[#0c0d0e] rounded p-6 text-center">
            <div className="text-xs font-medium text-[#ededed]">
              Select Handwritten Answer Sheet (PDF or Image)
            </div>
            <p className="text-[11px] text-[#555963] mt-0.5 mb-3 font-mono">
              Upload one student's handwritten paper to run end-to-end evaluation
            </p>
            <input
              type="file"
              multiple
              accept=".pdf,.jpg,.jpeg,.png"
              onChange={handleFileSelection}
              className="text-xs text-[#717680] file:mr-2 file:py-1.5 file:px-3 file:rounded file:border file:border-[#2e323b] file:bg-[#1a1c21] file:text-[#b0b4be] file:text-xs file:font-mono hover:file:bg-[#22252c] cursor-pointer"
            />
          </div>

          {/* Student Metadata Table */}
          {files.length > 0 && (
            <div className="space-y-2 pt-2">
              <div className="flex items-center justify-between text-[11px] font-mono text-[#717680]">
                <span>Student Paper Details ({files.length} paper{files.length > 1 ? 's' : ''})</span>
                <span>Confirm Roll No / Name</span>
              </div>

              <div className="overflow-x-auto rounded border border-[#22242a] bg-[#0c0d0e]">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-[#22242a] text-[#8c9099] font-mono text-[11px] bg-[#14161a]">
                      <th className="py-2 px-3 font-medium">File</th>
                      <th className="py-2 px-3 font-medium">Hall Ticket / Roll No.</th>
                      <th className="py-2 px-3 font-medium">Student Name</th>
                      <th className="py-2 px-3 text-center w-12 font-medium"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#1a1c21]">
                    {studentMeta.map((m, idx) => (
                      <tr key={idx} className="hover:bg-[#121316]">
                        <td className="py-1.5 px-3 text-[#717680] font-mono text-[11px] truncate max-w-[200px]">
                          {m.filename}
                        </td>
                        <td className="py-1.5 px-3">
                          <input
                            type="text"
                            value={m.hall_ticket}
                            onChange={(e) => handleMetaChange(idx, 'hall_ticket', e.target.value)}
                            className="w-full bg-[#121316] border border-[#22242a] rounded px-2 py-0.5 text-xs text-[#ededed] font-mono focus:outline-none"
                          />
                        </td>
                        <td className="py-1.5 px-3">
                          <input
                            type="text"
                            value={m.name}
                            onChange={(e) => handleMetaChange(idx, 'name', e.target.value)}
                            className="w-full bg-[#121316] border border-[#22242a] rounded px-2 py-0.5 text-xs text-[#ededed] focus:outline-none"
                          />
                        </td>
                        <td className="py-1.5 px-3 text-center">
                          <button
                            type="button"
                            onClick={() => handleRemoveFile(idx)}
                            className="text-[#555963] hover:text-[#ef4444] text-xs"
                          >
                            ×
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* Action Buttons */}
        {files.length > 0 && (
          <div className="flex items-center justify-end gap-3">
            {files.length === 1 && (
              <button
                type="button"
                onClick={handleSingleStudentEvaluation}
                disabled={evaluatingSingle || !rubricInfo}
                className="bg-[#10b981] hover:bg-[#059669] text-white font-medium py-2 px-5 rounded text-xs font-mono transition-colors cursor-pointer disabled:opacity-50"
              >
                {evaluatingSingle ? 'Evaluating with Local AI...' : '⚡ Evaluate Answer Sheet (Immediate)'}
              </button>
            )}

            <button
              type="button"
              onClick={handleUpload}
              disabled={uploading || !rubricInfo}
              className="bg-[#2563eb] hover:bg-[#1d4ed8] text-white font-medium py-2 px-5 rounded text-xs font-mono transition-colors cursor-pointer disabled:opacity-50"
            >
              {uploading ? 'Dispatching...' : `Submit to Queue (${files.length} Papers)`}
            </button>
          </div>
        )}
      </div>

      {/* Immediate Evaluation Result Report (Phase 8) */}
      {singleResult && (
        <div className="mt-8 bg-[#121316] border border-[#26282f] rounded p-6 space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 border-b border-[#22242a] gap-2">
            <div>
              <div className="text-[11px] font-mono text-[#717680] uppercase">
                Official Evaluation Report
              </div>
              <h2 className="text-base font-bold text-[#ededed] mt-0.5">
                {singleResult.student_name} ({singleResult.hall_ticket})
              </h2>
              <p className="text-xs font-mono text-[#8c9099]">
                Exam: {singleResult.exam_id}
              </p>
            </div>

            <div className="flex items-center gap-4">
              <div className="text-right">
                <div className="text-2xl font-bold font-mono text-[#ededed]">
                  {singleResult.total_score} <span className="text-sm font-normal text-[#717680]">/ {singleResult.max_total}</span>
                </div>
                <div className="text-[11px] font-mono text-[#8c9099]">
                  {((singleResult.total_score / (singleResult.max_total || 1)) * 100).toFixed(1)}% Marks
                </div>
              </div>

              <div>
                <ConfidenceBadge status={singleResult.status} confidence={singleResult.avg_confidence} />
              </div>
            </div>
          </div>

          {/* Question-wise Breakdown */}
          <div className="space-y-4">
            <h3 className="text-xs font-mono uppercase tracking-wider text-[#8c9099] font-medium">
              Question-wise Detailed Breakdown
            </h3>

            {singleResult.questions?.map((q, idx) => (
              <div key={idx} className="bg-[#0c0d0e] border border-[#22242a] rounded p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold text-xs text-[#ededed]">
                      Q{q.question_number}
                    </span>
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#181a1f] text-[#8c9099] border border-[#26282f]">
                      {q.question_type}
                    </span>
                    {q.model_used && (
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#141d26] text-[#38bdf8] border border-[#1e2e3e]">
                        {q.model_used}
                      </span>
                    )}
                  </div>

                  <div className="text-xs font-mono font-bold text-[#ededed]">
                    {q.final_score} / {q.max_marks} marks
                  </div>
                </div>

                {/* Extracted student text */}
                <div>
                  <div className="text-[11px] font-mono text-[#717680] mb-1">Student's Extracted Answer:</div>
                  <div className="bg-[#121316] border border-[#1e2026] rounded p-2.5 text-xs text-[#c4c7cf] font-mono whitespace-pre-wrap leading-relaxed">
                    {q.extracted_text || '(No answer text extracted for this question)'}
                  </div>
                </div>

                {/* Evidence & Criteria Table */}
                {q.criteria && q.criteria.length > 0 && (
                  <div className="pt-2">
                    <EvidenceTable criteria={q.criteria} />
                  </div>
                )}

                {/* Confidence & Routing Warning */}
                <div className="flex items-center justify-between pt-2 border-t border-[#181a1f] text-[11px] font-mono">
                  <span className="text-[#717680]">
                    Confidence: {(q.c_final * 100).toFixed(0)}% (OCR: {(q.c_ocr * 100).toFixed(0)}%, Agree: {(q.c_agree * 100).toFixed(0)}%, Evid: {(q.c_evid * 100).toFixed(0)}%)
                  </span>
                  <span className={q.routing === 'AUTO_ACCEPT' ? 'text-[#4ade80]' : 'text-[#f59e0b]'}>
                    {q.routing === 'AUTO_ACCEPT' ? '✓ Auto-Accepted' : '⚠️ Flagged for Teacher Review'}
                  </span>
                </div>
              </div>
            ))}
          </div>

          <div className="pt-2 text-right">
            <button
              onClick={onGoToDashboard}
              className="px-4 py-2 rounded text-xs font-mono bg-[#2563eb] hover:bg-[#1d4ed8] text-white transition-colors"
            >
              Open in Evaluation Dashboard →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
