/**
 * AutoGrade — API Client
 * 
 * Handles all Axios HTTP requests to the FastAPI backend.
 */

import axios from 'axios';

const BASE_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000';

const client = axios.create({
  baseURL: BASE_URL,
  timeout: 300000, // 5 minutes for heavy OCR/RAG operations
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

  // Rubric Approval
  approveRubric: async (examId, rubric) => {
    const res = await client.post('/exam/rubric/approve', {
      exam_id: examId,
      rubric: rubric,
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
  confirmQuestion: async (examId, hallTicket, questionNumber) => {
    const res = await client.post('/review/confirm', {
      exam_id: examId,
      hall_ticket: hallTicket,
      question_number: Number(questionNumber),
    });
    return res.data;
  },

  editQuestionScore: async (examId, hallTicket, questionNumber, newScore, editReason = '') => {
    const res = await client.post('/review/edit', {
      exam_id: examId,
      hall_ticket: hallTicket,
      question_number: Number(questionNumber),
      new_score: Number(newScore),
      edit_reason: editReason,
    });
    return res.data;
  },

  // Export Download URL
  getExportUrl: (examId) => `${BASE_URL}/export/${examId}`,
};

export default api;
