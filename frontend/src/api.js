/**
 * AutoGrade API client: every request the pages make to the FastAPI backend.
 * The session is an HttpOnly cookie, so every call sends credentials.
 */

import axios from 'axios';

const BASE_URL = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000';

const client = axios.create({
  baseURL: BASE_URL,
  withCredentials: true,
  timeout: 300000, // Reading a question paper can take a while; sheets are graded in the background.
});

const id = (value) => encodeURIComponent(value);

export const api = {
  // ---------------------------------------------------------------- sign-in
  authStatus: async () => (await client.get('/auth/status')).data,
  login: async (password) => (await client.post('/auth/login', { password })).data,
  logout: async () => (await client.post('/auth/logout')).data,

  // ------------------------------------------------- exams and question papers
  getExams: async () => (await client.get('/exam')).data,

  setupExam: async (formData) => (await client.post('/exam/setup', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })).data,

  // Draft marking points from a CSV or text answer key. Nothing is approved here.
  draftCriteria: async (examId, answerKeyFile) => {
    const formData = new FormData();
    formData.append('answer_key', answerKeyFile);
    return (await client.post(`/exam/${id(examId)}/rubric/draft-criteria`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })).data;
  },

  getExamRubric: async (examId) => (await client.get(`/exam/${id(examId)}/rubric`)).data,

  approveRubric: async (examId, rubric, docFingerprint = '', genFingerprint = '', totalMarks = null, teacherConfirmed = false, marking = {}) => (
    await client.post('/exam/rubric/approve', {
      exam_id: examId,
      rubric,
      doc_fingerprint: docFingerprint,
      gen_fingerprint: genFingerprint,
      total_marks: totalMarks !== null ? Number(totalMarks) : null,
      teacher_confirmed: teacherConfirmed,
      marking_style: marking.style || '',
      marking_notes: marking.notes || '',
    })
  ).data,

  // Deletes the exam: its paper, approved marks, calibration and every answer sheet.
  deleteExam: async (examId) => (await client.delete(`/exam/${id(examId)}`)).data,

  // Deletes only the question paper and its approved marks. Refused while sheets depend on it.
  deletePaper: async (examId) => (await client.delete(`/exam/${id(examId)}/paper`)).data,

  // ------------------------------------------------------------ answer sheets
  uploadAnswerSheets: async (formData) => (await client.post('/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })).data,

  evaluateSingleStudent: async (formData, onUploadProgress) => (await client.post('/evaluate', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
    onUploadProgress,
  })).data,

  getEvaluationJob: async (jobId) => (await client.get(`/evaluate/jobs/${id(jobId)}`, { timeout: 15000 })).data,
  retryEvaluationJob: async (jobId) => (await client.post(`/evaluate/jobs/${id(jobId)}/retry`, null, { timeout: 15000 })).data,
  cancelEvaluationJob: async (jobId) => (await client.post(`/evaluate/jobs/${id(jobId)}/cancel`, null, { timeout: 15000 })).data,

  getQueueStatus: async (examId) => (await client.get(`/queue/${id(examId)}`)).data,
  resumeQueue: async (examId) => (await client.post(`/queue/${id(examId)}/resume`)).data,

  getExamResults: async (examId) => (await client.get(`/results/${id(examId)}`)).data,

  // Deletes one sheet everywhere: marks, review notes, stored pages, queue entry.
  deleteSheet: async (examId, hallTicket) => (await client.delete(`/results/${id(examId)}/${id(hallTicket)}`)).data,

  // ------------------------------------------------------------------ review
  confirmQuestion: async (examId, hallTicket, questionNumber, subPart = '') => (
    await client.post('/review/confirm', {
      exam_id: examId,
      hall_ticket: hallTicket,
      question_number: Number(questionNumber),
      sub_part: subPart,
    })
  ).data,

  saveAnswerEdit: async (payload) => (await client.post('/review/edit', payload)).data,

  confirmBlanks: async (examId, hallTicket) => (
    await client.post('/review/confirm-blanks', { exam_id: examId, hall_ticket: hallTicket })
  ).data,

  approveSheet: async (examId, hallTicket, unmatchedChecked = false) => (
    await client.post('/review/approve', { exam_id: examId, hall_ticket: hallTicket, unmatched_checked: unmatchedChecked })
  ).data,

  approveUnflagged: async (examId) => (await client.post(`/review/approve-unflagged/${id(examId)}`)).data,

  getReviewSourcePageUrl: (examId, hallTicket, pageNumber) => (
    `${BASE_URL}/review/source-page/${id(examId)}/${id(hallTicket)}/${Number(pageNumber)}`
  ),

  // ------------------------------------------------------------- calibration
  getCalibration: async (examId) => (await client.get(`/calibration/${id(examId)}`)).data,

  // Learns from this marked sheet. Approves it first if every answer is decided.
  calibrateWithSheet: async (examId, hallTicket, unmatchedChecked = false) => (
    await client.post(`/calibration/${id(examId)}/sheet/${id(hallTicket)}`, { unmatched_checked: unmatchedChecked })
  ).data,

  stopCalibration: async (examId) => (await client.delete(`/calibration/${id(examId)}`)).data,

  // Marks again, to the calibration, the sheets the teacher has not touched. One Gemini request each.
  remarkWithCalibration: async (examId) => (await client.post(`/calibration/${id(examId)}/remark`)).data,

  // ------------------------------------------------------------------ export
  // Refuses with 409 while sheets are not final, unless the marked copy is asked for.
  downloadExport: async (examId, includeNotFinal = false) => client.get(`/export/${id(examId)}`, {
    params: includeNotFinal ? { include_not_final: true } : {},
    responseType: 'blob',
  }),
};

export default api;
