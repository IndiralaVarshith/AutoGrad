/**
 * AutoGrade — API Client
 * 
 * Handles all Axios HTTP requests to the FastAPI backend.
 */

import axios from 'axios';

const BASE_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000';

const client = axios.create({
  baseURL: BASE_URL,
  timeout: 300000, // Legacy setup operations; sheet grading uses a background job.
});

export const api = {
  // Check backend health
  checkHealth: async () => {
    const res = await client.get('/');
    return res.data;
  },

  // Exam Setup (RAG Rubric proposal)
  setupExam: async (formData) => {
    const res = await client.post('/exam/setup', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return res.data;
  },

  // Draft criteria from an answer key. Nothing is approved until the teacher approves it.
  draftCriteria: async (examId, answerKeyFile) => {
    const formData = new FormData();
    formData.append('answer_key', answerKeyFile);
    const res = await client.post(`/exam/${encodeURIComponent(examId)}/rubric/draft-criteria`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return res.data;
  },

  // List all configured exams
  getExams: async () => {
    const res = await client.get('/exam');
    return res.data;
  },

  // Get specific exam details
  getExamDetails: async (examId) => {
    const res = await client.get(`/exam/${examId}`);
    return res.data;
  },

  // Get approved rubric for exam
  getExamRubric: async (examId) => {
    const res = await client.get(`/exam/${examId}/rubric`);
    return res.data;
  },

  // Rubric Approval
  approveRubric: async (examId, rubric, docFingerprint = '', genFingerprint = '', totalMarks = null, teacherConfirmed = false) => {
    const res = await client.post('/exam/rubric/approve', {
      exam_id: examId,
      rubric: rubric,
      doc_fingerprint: docFingerprint,
      gen_fingerprint: genFingerprint,
      total_marks: totalMarks !== null ? Number(totalMarks) : null,
      teacher_confirmed: teacherConfirmed,
    });
    return res.data;
  },

  // Single student evaluation is accepted quickly and processed in the background.
  getQueueStatus: async (examId) => {
    const res = await client.get(`/queue/${encodeURIComponent(examId)}`);
    return res.data;
  },

  resumeQueue: async (examId) => {
    const res = await client.post(`/queue/${encodeURIComponent(examId)}/resume`);
    return res.data;
  },

  evaluateSingleStudent: async (formData, onUploadProgress) => {
    const res = await client.post('/evaluate', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
      onUploadProgress,
    });
    return res.data;
  },

  getEvaluationJob: async (jobId) => {
    const res = await client.get(`/evaluate/jobs/${encodeURIComponent(jobId)}`, {
      timeout: 15000,
    });
    return res.data;
  },

  retryEvaluationJob: async (jobId) => {
    const res = await client.post(`/evaluate/jobs/${encodeURIComponent(jobId)}/retry`, null, {
      timeout: 15000,
    });
    return res.data;
  },

  cancelEvaluationJob: async (jobId) => {
    const res = await client.post(`/evaluate/jobs/${encodeURIComponent(jobId)}/cancel`, null, {
      timeout: 15000,
    });
    return res.data;
  },

  // Bulk Upload Answer Sheets
  uploadAnswerSheets: async (formData) => {
    const res = await client.post('/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    return res.data;
  },

  // Results
  getExamResults: async (examId) => {
    const res = await client.get(`/results/${examId}`);
    return res.data;
  },

  getStudentResult: async (examId, hallTicket) => {
    const res = await client.get(`/results/${examId}/${hallTicket}`);
    return res.data;
  },

  // Teacher Review actions
  confirmQuestion: async (examId, hallTicket, questionNumber, subPart = '') => {
    const res = await client.post('/review/confirm', {
      exam_id: examId,
      hall_ticket: hallTicket,
      question_number: Number(questionNumber),
      sub_part: subPart,
    });
    return res.data;
  },

  editQuestionScore: async (examId, hallTicket, questionNumber, newScore, editReason = '', subPart = '') => {
    const res = await client.post('/review/edit', {
      exam_id: examId,
      hall_ticket: hallTicket,
      question_number: Number(questionNumber),
      sub_part: subPart,
      new_score: Number(newScore),
      edit_reason: editReason,
    });
    return res.data;
  },

  reviewOCRRegion: async (payload) => {
    const res = await client.post('/review/ocr-region', payload);
    return res.data;
  },

  reviewOCRPage: async (payload) => {
    const res = await client.post('/review/ocr-page', payload);
    return res.data;
  },

  regradeValidatedOCR: async (payload) => {
    const res = await client.post('/review/ocr-regrade', payload);
    return res.data;
  },

  getReviewSourcePageUrl: (examId, hallTicket, pageNumber) =>
    `${BASE_URL}/review/source-page/${encodeURIComponent(examId)}/${encodeURIComponent(hallTicket)}/${Number(pageNumber)}`,

  // Export Download URL
  getExportUrl: (examId) => `${BASE_URL}/export/${examId}`,
};

export default api;
