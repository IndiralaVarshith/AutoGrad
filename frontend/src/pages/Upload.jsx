import React, { useState, useEffect } from 'react';
import api from '../api';
import QueueStatus from '../components/QueueStatus';

export default function Upload({ examId = 'EXAM_2026_01', onGoToDashboard }) {
  const [selectedExamId, setSelectedExamId] = useState(examId);
  const [files, setFiles] = useState([]);
  const [studentMeta, setStudentMeta] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');
  const [error, setError] = useState('');
  const [queueStats, setQueueStats] = useState({ total: 0, processing: 0, completed: 0, needsReview: 0 });

  useEffect(() => {
    if (examId) setSelectedExamId(examId);
  }, [examId]);

  const handleFileSelection = (e) => {
    const fileList = Array.from(e.target.files);
    if (fileList.length === 0) return;

    setFiles(fileList);

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
  };

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
            Answer Sheet Ingestion
          </h1>
          <p className="text-xs text-[#717680] mt-0.5 font-mono">
            Upload student handwritten sheets for automated evaluation
          </p>
        </div>

        {onGoToDashboard && (
          <button
            onClick={onGoToDashboard}
            className="px-3 py-1.5 rounded text-xs font-mono bg-[#16181d] hover:bg-[#202227] text-[#c4c7cf] border border-[#26282f] transition-colors"
          >
            View Results →
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
      <form onSubmit={handleUpload} className="space-y-5">
        <div className="bg-[#121316] border border-[#22242a] rounded p-5 space-y-4">
          <div className="flex items-center justify-between">
            <div className="text-xs font-mono uppercase tracking-wider text-[#8c9099] font-medium">
              Queue Target
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

          {/* Flat Drop Area */}
          <div className="border border-[#25282f] bg-[#0c0d0e] rounded p-6 text-center">
            <div className="text-xs font-medium text-[#ededed]">
              Select Handwritten Answer Sheets
            </div>
            <p className="text-[11px] text-[#555963] mt-0.5 mb-3 font-mono">
              Accepts PDF, JPG, PNG files
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
                <span>Student Hall Ticket Mapping ({files.length} papers)</span>
                <span>Edit values if needed</span>
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

        {files.length > 0 && (
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={uploading}
              className="bg-[#2563eb] hover:bg-[#1d4ed8] text-white font-medium py-2 px-5 rounded text-xs font-mono transition-colors cursor-pointer disabled:opacity-50"
            >
              {uploading ? 'Dispatching...' : `Submit ${files.length} Papers for Evaluation`}
            </button>
          </div>
        )}
      </form>
    </div>
  );
}
