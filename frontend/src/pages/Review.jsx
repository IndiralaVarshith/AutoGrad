import React, { useState, useEffect } from 'react';
import api from '../api';
import ConfidenceBadge from '../components/ConfidenceBadge';
import EvidenceTable from '../components/EvidenceTable';

export default function Review({ examId = 'EXAM_2026_01', initialHallTicket = null, onBackToDashboard }) {
  const [currentExamId, setCurrentExamId] = useState(examId);
  const [flaggedPapers, setFlaggedPapers] = useState([]);
  const [currentPaperIndex, setCurrentPaperIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [editStates, setEditStates] = useState({});
  const [statusMessage, setStatusMessage] = useState('');
  const [selectedOCRRegion, setSelectedOCRRegion] = useState(null);
  const [ocrRegionDraft, setOCRRegionDraft] = useState({ text: '', questionMapping: '' });
  const [ocrPageMappings, setOCRPageMappings] = useState({});
  const [ocrActionLoading, setOCRActionLoading] = useState(false);

  const loadFlaggedPapers = async () => {
    try {
      setLoading(true);
      const res = await api.getExamResults(currentExamId);
      const allResults = res.results || [];
      const flagged = allResults.filter(
        (p) => p.status === 'NEEDS_REVIEW' || (p.flagged_count && p.flagged_count > 0)
      );

      setFlaggedPapers(flagged);

      if (initialHallTicket) {
        const foundIdx = flagged.findIndex((p) => p.hall_ticket === initialHallTicket);
        if (foundIdx !== -1) setCurrentPaperIndex(foundIdx);
      }
    } catch (err) {
      console.error('Failed to load flagged papers:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFlaggedPapers();
  }, [currentExamId]);

  const activePaper = flaggedPapers[currentPaperIndex];
  const questionKey = (question) => `${question.question_number}:${question.sub_part || ''}`;

  const selectOCRRegion = (pageNumber, region) => {
    const currentMapping = region.teacher_question_number
      ? `${region.teacher_question_number}:${region.teacher_sub_part || ''}`
      : (region.question_number_candidate ? `${region.question_number_candidate}:` : '');
    setSelectedOCRRegion({ pageNumber, regionId: region.region_id });
    setOCRRegionDraft({
      text: region.teacher_corrected_text ?? region.extracted_text ?? '',
      questionMapping: currentMapping,
    });
  };

  const openOCRRegion = (regionId) => {
    const page = (activePaper?.ocr_pages || []).find((item) =>
      (item.regions || []).some((region) => region.region_id === regionId)
    );
    const region = page?.regions?.find((item) => item.region_id === regionId);
    if (!page || !region) return;
    selectOCRRegion(page.page_number, region);
    document.getElementById(`ocr-page-${page.page_number}`)?.scrollIntoView({ behavior: 'smooth' });
  };

  const handleOCRRegionSave = async (action) => {
    if (!activePaper || !selectedOCRRegion) return;
    const [questionNumber, subPart = ''] = ocrRegionDraft.questionMapping.split(':');
    try {
      setOCRActionLoading(true);
      setStatusMessage('');
      await api.reviewOCRRegion({
        exam_id: currentExamId,
        hall_ticket: activePaper.hall_ticket,
        page_number: selectedOCRRegion.pageNumber,
        region_id: selectedOCRRegion.regionId,
        action,
        corrected_text: action === 'CORRECT' ? ocrRegionDraft.text : '',
        question_number: questionNumber ? Number(questionNumber) : null,
        sub_part: subPart,
      });
      setStatusMessage(`OCR region ${action.toLowerCase()} saved. Any mapped question remains provisional until its score is reviewed.`);
      setSelectedOCRRegion(null);
      await loadFlaggedPapers();
    } catch (err) {
      alert(err.response?.data?.detail || 'Could not save the OCR review.');
    } finally {
      setOCRActionLoading(false);
    }
  };

  const handleOCRPageReview = async (page, action) => {
    if (!activePaper) return;
    const [questionNumber, subPart = ''] = (ocrPageMappings[page.page_number] || '').split(':');
    try {
      setOCRActionLoading(true);
      await api.reviewOCRPage({
        exam_id: currentExamId,
        hall_ticket: activePaper.hall_ticket,
        page_number: page.page_number,
        action,
        question_number: action === 'UNREADABLE' && questionNumber ? Number(questionNumber) : null,
        sub_part: subPart,
      });
      setStatusMessage(`Page ${page.page_number} disposition saved.`);
      await loadFlaggedPapers();
    } catch (err) {
      alert(err.response?.data?.detail || 'Could not save the page review.');
    } finally {
      setOCRActionLoading(false);
    }
  };

  const handleConfirm = async (question) => {
    if (!activePaper) return;
    const key = questionKey(question);
    try {
      setActionLoading(key);
      setStatusMessage('');
      await api.confirmQuestion(currentExamId, activePaper.hall_ticket, question.question_number, question.sub_part || '');

      setStatusMessage(`Question Q${question.question_number}${question.sub_part || ''} confirmed.`);
      await loadFlaggedPapers();
    } catch (err) {
      console.error('Confirm error:', err);
      alert(err.response?.data?.detail || 'Failed to confirm question.');
    } finally {
      setActionLoading(null);
    }
  };

  const handleOCRRegrade = async (question) => {
    if (!activePaper) return;
    const key = questionKey(question);
    try {
      setActionLoading(key);
      setStatusMessage('');
      const result = await api.regradeValidatedOCR({
        exam_id: currentExamId,
        hall_ticket: activePaper.hall_ticket,
        question_number: question.question_number,
        sub_part: question.sub_part || '',
      });
      setStatusMessage(
        `Validated text graded for Q${question.question_number}${question.sub_part || ''}: ${result.final_score} / ${result.max_marks}. Confirm the score after review.`
      );
      await loadFlaggedPapers();
    } catch (err) {
      alert(err.response?.data?.detail || 'Could not regrade the validated answer.');
    } finally {
      setActionLoading(null);
    }
  };

  const handleEditSubmit = async (question) => {
    if (!activePaper) return;
    const key = questionKey(question);
    const editState = editStates[key];
    if (!editState || editState.score === '') {
      alert('Please enter a valid numeric score.');
      return;
    }

    try {
      setActionLoading(key);
      setStatusMessage('');
      await api.editQuestionScore(
        currentExamId,
        activePaper.hall_ticket,
        question.question_number,
        Number(editState.score),
        editState.reason || 'Faculty override',
        question.sub_part || ''
      );

      setStatusMessage(`Question Q${question.question_number}${question.sub_part || ''} score updated to ${editState.score}.`);
      setEditStates((prev) => ({
        ...prev,
        [key]: { ...prev[key], open: false },
      }));
      await loadFlaggedPapers();
    } catch (err) {
      console.error('Edit error:', err);
      alert(err.response?.data?.detail || 'Failed to override score.');
    } finally {
      setActionLoading(null);
    }
  };

  const toggleEditForm = (question) => {
    const key = questionKey(question);
    setEditStates((prev) => ({
      ...prev,
      [key]: {
        score: prev[key]?.score ?? String(question.suggested_score ?? question.final_score),
        reason: prev[key]?.reason ?? '',
        open: !prev[key]?.open,
      },
    }));
  };

  if (loading && flaggedPapers.length === 0) {
    return (
      <div className="max-w-4xl mx-auto py-16 text-center text-xs font-mono text-[#717680]">
        Loading flagged papers...
      </div>
    );
  }

  if (flaggedPapers.length === 0) {
    return (
      <div className="max-w-md mx-auto py-16 px-4 text-center space-y-4">
        <div className="text-sm font-semibold text-[#4ade80] font-mono uppercase tracking-wider">
          Review Queue Clean
        </div>
        <p className="text-xs text-[#8c9099] leading-relaxed">
          No papers in exam <span className="font-mono text-[#ededed]">{currentExamId}</span> currently require manual verification.
        </p>
        <div className="pt-2">
          <button
            onClick={onBackToDashboard}
            className="bg-[#181a1d] hover:bg-[#202227] text-[#c4c7cf] border border-[#272a31] py-2 px-4 rounded text-xs font-mono transition-colors cursor-pointer"
          >
            ← Return to Results
          </button>
        </div>
      </div>
    );
  }

  const flaggedQuestions = (activePaper?.questions || []).filter(
    (q) => (q.routing === 'FLAGGED' || q.routing === 'MANUAL') && !q.teacher_confirmed
  );
  const reviewQuestionOptions = activePaper?.questions || [];
  const selectedPage = (activePaper?.ocr_pages || []).find(
    (page) => page.page_number === selectedOCRRegion?.pageNumber
  );
  const selectedRegion = selectedPage?.regions?.find(
    (region) => region.region_id === selectedOCRRegion?.regionId
  );

  return (
    <div className="max-w-5xl mx-auto py-6 px-4 sm:px-6 space-y-5">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-[#22242a] pb-4">
        <div className="flex items-center gap-3">
          <button
            onClick={onBackToDashboard}
            className="px-2.5 py-1 rounded bg-[#16181d] hover:bg-[#202227] text-[#8c9099] border border-[#25282f] text-xs font-mono transition-colors"
          >
            ← Back
          </button>
          <div>
            <h1 className="text-lg font-bold text-[#ededed] tracking-tight">
              Evaluation Review
            </h1>
            <p className="text-xs text-[#717680] font-mono">
              Manual verification for disputed criteria and low-confidence markings
            </p>
          </div>
        </div>

        {/* Paper Pagination */}
        <div className="flex items-center gap-2 font-mono text-xs bg-[#121316] border border-[#22242a] px-2.5 py-1 rounded">
          <button
            disabled={currentPaperIndex === 0}
            onClick={() => setCurrentPaperIndex((prev) => prev - 1)}
            className="text-[#8c9099] hover:text-[#ededed] disabled:opacity-30 cursor-pointer"
          >
            ← Prev
          </button>
          <span className="text-[#717680]">
            {currentPaperIndex + 1} / {flaggedPapers.length}
          </span>
          <button
            disabled={currentPaperIndex === flaggedPapers.length - 1}
            onClick={() => setCurrentPaperIndex((prev) => prev + 1)}
            className="text-[#8c9099] hover:text-[#ededed] disabled:opacity-30 cursor-pointer"
          >
            Next →
          </button>
        </div>
      </div>

      {statusMessage && (
        <div className="p-2.5 rounded bg-[#112217] border border-[#1b3825] text-[#4ade80] text-xs font-mono">
          {statusMessage}
        </div>
      )}

      {/* Active Student Banner */}
      <div className="bg-[#121316] border border-[#22242a] rounded p-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono font-semibold text-[#ededed]">
              {activePaper.hall_ticket}
            </span>
            <span className="text-xs text-[#8c9099]">
              {activePaper.student_name || 'Candidate'}
            </span>
          </div>
          <p className="text-[11px] font-mono text-[#555963] mt-0.5">
            Total Score: {activePaper.total_score} / {activePaper.max_total}
          </p>
        </div>

        <div className="text-right font-mono text-xs text-[#f59e0b]">
          {flaggedQuestions.length} flagged questions
          <div className="mt-1 text-[10px] text-[#fbbf24]">
            {activePaper.finality_status || 'PENDING_VALIDATION'}
          </div>
        </div>
      </div>

      {(activePaper.ocr_pages || []).length > 0 && (
        <section className="bg-[#121316] border border-[#22242a] rounded p-4 space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-[#ededed]">Answer-sheet OCR evidence</h2>
            <p className="mt-1 text-[11px] text-[#fbbf24] leading-relaxed">
              OCR scores are uncalibrated. Inspect the original page and validate each region before the result can be final.
            </p>
          </div>

          {(activePaper.unmapped_answers || []).length > 0 && (
            <div className="space-y-2 rounded border border-[#6b4e1f] bg-[#1b160c] p-3">
              <div className="text-[10px] font-mono uppercase tracking-wider text-[#fbbf24]">
                OCR fragments requiring question-mapping review
              </div>
              {(activePaper.unmapped_answers || []).map((answer, index) => {
                const pageNumber = answer.page_references?.[0]?.page_number;
                return (
                  <div key={`${answer.mapping_status}-${pageNumber || 0}-${index}`} className="rounded border border-[#40341e] p-2">
                    <div className="flex flex-wrap items-center gap-2 text-[10px] font-mono text-[#d8c79e]">
                      <span>{answer.detected_question_number ? `Detected Q${answer.detected_question_number}${answer.sub_part || ''}` : 'Question number unresolved'}</span>
                      {answer.candidate_question_number && <span>Possible continuation of Q{answer.candidate_question_number}</span>}
                      <span>{answer.mapping_status || 'UNRESOLVED'}</span>
                      {pageNumber && (
                        <button
                          type="button"
                          onClick={() => document.getElementById(`ocr-page-${pageNumber}`)?.scrollIntoView({ behavior: 'smooth' })}
                          className="underline text-[#93c5fd]"
                        >
                          Inspect page {pageNumber}
                        </button>
                      )}
                    </div>
                    {answer.extracted_text && (
                      <div className="mt-1 whitespace-pre-wrap text-xs text-[#c4c7cf]">{answer.extracted_text}</div>
                    )}
                    {(answer.illegible_regions || []).length > 0 && (
                      <div className="mt-1 text-[10px] text-[#fbbf24]">{answer.illegible_regions.join(' · ')}</div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <div className="space-y-4">
            {activePaper.ocr_pages.map((page) => (
              <div id={`ocr-page-${page.page_number}`} key={page.page_number} className="rounded border border-[#25282f] bg-[#0c0d0e] p-3 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-mono text-xs text-[#ededed]">
                    Page {page.page_number}
                    <span className="ml-2 text-[10px] text-[#717680]">
                      {page.status} · {page.regions?.length || 0} regions
                    </span>
                  </div>
                  {page.status === 'BLANK_CANDIDATE' && (
                    <button
                      type="button"
                      disabled={ocrActionLoading}
                      onClick={() => handleOCRPageReview(page, 'ACCEPT_BLANK')}
                      className="px-2.5 py-1 rounded border border-[#315a3d] text-[#86efac] text-[10px] font-mono disabled:opacity-50"
                    >
                      Confirm blank page
                    </button>
                  )}
                </div>

                {(page.status === 'UNAVAILABLE' || page.status === 'UNREADABLE') && (
                  <div className="flex flex-wrap items-end gap-2">
                    <label className="text-[10px] text-[#8c9099]">
                      Question affected
                      <select
                        value={ocrPageMappings[page.page_number] || ''}
                        onChange={(event) => setOCRPageMappings((prev) => ({ ...prev, [page.page_number]: event.target.value }))}
                        className="ml-2 bg-[#121316] border border-[#343842] rounded px-2 py-1 text-xs text-[#ededed]"
                      >
                        <option value="">Select question…</option>
                        {reviewQuestionOptions.map((question) => (
                          <option key={questionKey(question)} value={questionKey(question)}>
                            Q{question.question_number}{question.sub_part || ''}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      disabled={ocrActionLoading || !ocrPageMappings[page.page_number]}
                      onClick={() => handleOCRPageReview(page, 'UNREADABLE')}
                      className="px-2.5 py-1 rounded border border-[#67343a] text-[#fca5a5] text-[10px] font-mono disabled:opacity-50"
                    >
                      Mark page unreadable
                    </button>
                    {page.error && <span className="text-[10px] text-[#fca5a5]">{page.error}</span>}
                  </div>
                )}

                <div className="relative w-full overflow-hidden rounded border border-[#282a30] bg-black">
                  <img
                    src={api.getReviewSourcePageUrl(currentExamId, activePaper.hall_ticket, page.page_number)}
                    alt={`Original answer sheet page ${page.page_number}`}
                    className="block w-full h-auto"
                  />
                  <div className="absolute inset-0">
                    {(page.regions || []).map((region) => {
                      if (!region.bbox || region.bbox.length !== 4) return null;
                      const [x0, y0, x1, y1] = region.bbox;
                      const selected = selectedOCRRegion?.regionId === region.region_id;
                      const lowRawScore = typeof region.engine_score === 'number' && region.engine_score < 0.7;
                      const tone = lowRawScore ? 'border-rose-400 bg-rose-400/15' : 'border-amber-300 bg-amber-300/10';
                      return (
                        <button
                          key={region.region_id}
                          type="button"
                          title={`${region.uncertainty_class}${region.engine_score != null ? ` · raw score ${region.engine_score.toFixed(2)}` : ''}: ${region.extracted_text}`}
                          aria-label={`Review OCR region on page ${page.page_number}: ${region.extracted_text}`}
                          onClick={() => selectOCRRegion(page.page_number, region)}
                          className={`absolute border ${tone} ${selected ? 'ring-2 ring-white z-10' : ''}`}
                          style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${Math.max(0.5, (x1 - x0) * 100)}%`, height: `${Math.max(0.5, (y1 - y0) * 100)}%` }}
                        />
                      );
                    })}
                  </div>
                </div>

                <div className="max-h-52 overflow-y-auto divide-y divide-[#202228] rounded border border-[#22242a]">
                  {(page.regions || []).map((region) => (
                    <button
                      type="button"
                      key={region.region_id}
                      onClick={() => selectOCRRegion(page.page_number, region)}
                      className={`w-full text-left px-2.5 py-2 hover:bg-[#17191e] ${selectedOCRRegion?.regionId === region.region_id ? 'bg-[#1b1d23]' : ''}`}
                    >
                      <span className={`mr-2 text-[9px] font-mono ${typeof region.engine_score === 'number' && region.engine_score < 0.7 ? 'text-rose-300' : 'text-amber-300'}`}>
                        {region.uncertainty_class}{region.engine_score != null ? ` · RAW ${region.engine_score.toFixed(2)}` : ''} · {region.validation_status} · {region.question_mapping_status || 'UNRESOLVED'}
                      </span>
                      <span className="text-[11px] font-mono text-[#c4c7cf]">{region.extracted_text}</span>
                      <span className="ml-2 text-[9px] text-[#717680]">{region.region_id}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {selectedRegion && (
            <div className="rounded border border-[#3b3440] bg-[#141219] p-3 space-y-2.5">
              <div className="text-xs font-mono text-[#ededed]">
                Review page {selectedOCRRegion.pageNumber} · region {selectedRegion.region_id}
              </div>
              <div className="text-[10px] text-[#fbbf24]">{selectedRegion.uncertainty_reason}</div>
              <label className="block text-[10px] text-[#8c9099]">
                Corrected transcription
                <textarea
                  rows={3}
                  value={ocrRegionDraft.text}
                  onChange={(event) => setOCRRegionDraft((prev) => ({ ...prev, text: event.target.value }))}
                  className="mt-1 w-full bg-[#0c0d0e] border border-[#343842] rounded p-2 text-xs text-[#ededed] font-mono"
                />
              </label>
              <label className="block text-[10px] text-[#8c9099]">
                Question mapping · required before finalization
                <select
                  value={ocrRegionDraft.questionMapping}
                  onChange={(event) => setOCRRegionDraft((prev) => ({ ...prev, questionMapping: event.target.value }))}
                  className="mt-1 w-full bg-[#0c0d0e] border border-[#343842] rounded px-2 py-1 text-xs text-[#ededed]"
                >
                  <option value="">Unassigned — review mapping later</option>
                  {reviewQuestionOptions.map((question) => (
                    <option key={questionKey(question)} value={questionKey(question)}>
                      Q{question.question_number}{question.sub_part || ''}
                    </option>
                  ))}
                </select>
              </label>
              <div className="flex flex-wrap justify-end gap-2">
                <button type="button" disabled={ocrActionLoading} onClick={() => handleOCRRegionSave('UNREADABLE')} className="px-2.5 py-1 rounded border border-[#67343a] text-[#fca5a5] text-[10px] font-mono disabled:opacity-50">Mark unreadable</button>
                <button type="button" disabled={ocrActionLoading} onClick={() => handleOCRRegionSave('ACCEPT')} className="px-2.5 py-1 rounded border border-[#315a3d] text-[#86efac] text-[10px] font-mono disabled:opacity-50">Accept transcription</button>
                <button type="button" disabled={ocrActionLoading || !ocrRegionDraft.text.trim()} onClick={() => handleOCRRegionSave('CORRECT')} className="px-3 py-1 rounded bg-[#2563eb] text-white text-[10px] font-mono disabled:opacity-50">Save correction</button>
              </div>
            </div>
          )}
        </section>
      )}

      {/* Flagged Questions List */}
      <div className="space-y-4">
        {flaggedQuestions.map((q) => {
          const qNum = q.question_number;
          const qKey = questionKey(q);
          const qLabel = `${qNum}${q.sub_part || ''}`;
          const editState = editStates[qKey] || { score: String(q.suggested_score ?? q.final_score), reason: '', open: false };
          const isActing = actionLoading === qKey;

          return (
            <div key={qKey} className="bg-[#121316] border border-[#22242a] rounded p-5 space-y-4">
              {/* Question Header */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#1c1e23] pb-2.5">
                <div className="flex items-center gap-2 font-mono text-xs">
                  <span className="font-semibold text-[#ededed]">
                    Question {qLabel}
                  </span>
                  <span className="text-[#717680] text-[10px] border border-[#272a31] px-1 rounded">
                    {q.question_type}
                  </span>
                  <ConfidenceBadge routing={q.routing} score={q.c_final} size="sm" />
                  <span className="text-[9px] text-[#fbbf24]">OCR {q.ocr_confidence_class || 'UNCALIBRATED'}</span>
                </div>

                <div className="flex items-center gap-3 font-mono text-xs">
                  <div>
                    <span className="text-[#717680]">{q.suggested_score != null && !q.teacher_confirmed ? 'Draft: ' : 'Score: '}</span>
                    <span className="text-[#ededed] font-medium">{q.suggested_score != null && !q.teacher_confirmed ? q.suggested_score : q.final_score}</span>
                    <span className="text-[#555963]"> / {q.max_marks}</span>
                  </div>
                  <div className="text-[#555963] text-[11px]">
                    (Grader: {q.grader_score} | Critic: {q.critic_score})
                  </div>
                </div>
              </div>

              {/* OCR Text Box */}
              <div className="bg-[#0c0d0e] rounded p-3 border border-[#1f2126]">
                <div className="text-[10px] font-mono uppercase tracking-wider text-[#717680] mb-1">
                  Transcribed Student Text
                </div>
                <div className="text-xs font-mono text-[#c4c7cf] whitespace-pre-wrap leading-relaxed max-h-36 overflow-y-auto">
                  {q.extracted_text || <span className="text-[#555963] italic">No text extracted</span>}
                </div>
              </div>

              {(q.source_page_references || []).length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
                  <span className="text-[#717680]">Source pages:</span>
                  {(q.source_page_references || []).map((reference, index) => (
                    <button
                      key={`${reference.page_number}-${reference.reference_role || 'answer'}-${index}`}
                      type="button"
                      onClick={() => document.getElementById(`ocr-page-${reference.page_number}`)?.scrollIntoView({ behavior: 'smooth' })}
                      className="px-1.5 py-0.5 rounded border border-[#3b3440] text-[#93c5fd] hover:bg-[#17191e]"
                    >
                      Page {reference.page_number}{reference.reference_role === 'unresolved_possible_continuation' ? ' · possible continuation' : ''}
                    </button>
                  ))}
                  <span className="text-[#717680]">RapidOCR boxes are page candidates, not answer-aligned regions.</span>
                </div>
              )}

              {q.independent_verification && (
                <div className={`rounded p-3 border ${
                  q.verification_status === 'AGREEMENT'
                    ? 'bg-[#101b1a] border-[#1d3a35]'
                    : 'bg-[#211710] border-[#5b3a20]'
                }`}>
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] font-mono uppercase tracking-wider">
                    <span className="text-[#ededed]">Independent Gemini verification</span>
                    <span className={q.verification_status === 'AGREEMENT' ? 'text-[#63c7ad]' : 'text-[#fbbf24]'}>
                      {q.verification_status || q.independent_verification.status || 'REVIEW'}
                    </span>
                  </div>
                  <p className="mt-1 text-[10px] text-[#fbbf24]">
                    Gemini's result is separate evidence. It never changes the displayed marks automatically.
                  </p>
                  {q.independent_verification.transcription && (
                    <div className="mt-2 text-xs text-[#c4c7cf] whitespace-pre-wrap">
                      <span className="text-[#717680]">Independent reading: </span>
                      {q.independent_verification.transcription}
                    </div>
                  )}
                  {q.independent_verification.suggested_score != null && (
                    <div className="mt-1 text-xs font-mono text-[#c4c7cf]">
                      Suggested marks: {q.independent_verification.suggested_score} / {q.max_marks}
                    </div>
                  )}
                  {(q.independent_verification.comparison_reason || q.independent_verification.reason) && (
                    <p className="mt-1 text-[11px] text-[#8c9099]">
                      {q.independent_verification.comparison_reason || q.independent_verification.reason}
                    </p>
                  )}
                  {q.independent_verification.evidence_region_ids?.length > 0 && (
                    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
                      <span className="text-[#717680]">Image crops:</span>
                      {q.independent_verification.evidence_region_ids.map((regionId) => (
                        <button
                          key={regionId}
                          type="button"
                          onClick={() => openOCRRegion(regionId)}
                          className="px-1.5 py-0.5 rounded border border-[#3b3440] text-[#c4b5fd] hover:bg-[#211d29]"
                        >
                          {regionId}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {q.teacher_corrected_text && (
                <div className="bg-[#101b1a] rounded p-3 border border-[#1d3a35]">
                  <div className="text-[10px] font-mono uppercase tracking-wider text-[#63c7ad] mb-1">
                    Teacher-validated text · original OCR is preserved above
                  </div>
                  <div className="text-xs font-mono text-[#d7e7e2] whitespace-pre-wrap leading-relaxed max-h-36 overflow-y-auto">
                    {q.teacher_corrected_text}
                  </div>
                  {q.regrade_required && (
                    <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                      <span className="text-[10px] text-[#fbbf24]">
                        Regrade this teacher-validated transcript before confirming marks.
                      </span>
                      <button
                        type="button"
                        disabled={isActing || !q.teacher_corrected_text?.trim()}
                        onClick={() => handleOCRRegrade(q)}
                        className="px-2.5 py-1 rounded bg-[#176b60] hover:bg-[#1d8173] text-white text-[10px] font-mono disabled:opacity-50"
                      >
                        {isActing ? 'Regrading…' : 'Run grading on validated text'}
                      </button>
                    </div>
                  )}
                </div>
              )}

              {q.ocr_region_ids?.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
                  <span className="text-[#717680]">Supporting regions:</span>
                  {q.ocr_region_ids.map((regionId) => (
                    <button
                      key={regionId}
                      type="button"
                      onClick={() => openOCRRegion(regionId)}
                      className="px-1.5 py-0.5 rounded border border-[#3b3440] text-[#c4b5fd] hover:bg-[#211d29]"
                    >
                      {regionId}
                    </button>
                  ))}
                </div>
              )}

              {/* Evidence Table */}
              <div className="space-y-1">
                <div className="text-[10px] font-mono uppercase tracking-wider text-[#717680]">
                  Criteria & Evidence Assessment
                </div>
                  <EvidenceTable
                    criteria={q.criteria}
                    questionNumber={qLabel}
                    onOpenRegion={openOCRRegion}
                  />
              </div>

              {/* Review Decision Actions */}
              <div className="pt-2 border-t border-[#1c1e23] flex flex-wrap items-center justify-between gap-3">
                <div className="text-[11px] font-mono text-[#717680]">
                  {q.routing === 'FLAGGED' && 'Flagged due to critic challenge or citation match ratio.'}
                  {q.routing === 'MANUAL' && 'Low transcript clarity requires faculty evaluation.'}
                </div>

                <div className="flex items-center gap-2 font-mono">
                  <button
                    type="button"
                    disabled={isActing}
                    onClick={() => toggleEditForm(q)}
                    className="px-3 py-1 rounded text-xs bg-[#1a1c21] hover:bg-[#22242a] text-[#c4c7cf] border border-[#2b2e36] transition-colors cursor-pointer"
                  >
                    Edit Score
                  </button>

                  <button
                    type="button"
                    disabled={isActing || q.regrade_required}
                    onClick={() => handleConfirm(q)}
                    className="px-3.5 py-1 rounded text-xs bg-[#15803d] hover:bg-[#166534] text-white transition-colors cursor-pointer disabled:opacity-50"
                  >
                    {isActing ? 'Saving...' : `Confirm Score (${q.suggested_score ?? q.final_score})`}
                  </button>
                </div>
              </div>

              {/* Edit Score Drawer */}
              {editState.open && (
                <div className="p-3 bg-[#0c0d0e] rounded border border-[#25282f] space-y-2.5 font-mono">
                  <div className="text-xs text-[#ededed]">
                    Override Score for Q{qLabel}
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <div>
                      <label className="block text-[10px] text-[#717680] mb-0.5">New Score (Max {q.max_marks})</label>
                      <input
                        type="number"
                        step="0.5"
                        min="0"
                        max={q.max_marks}
                        value={editState.score}
                        onChange={(e) =>
                          setEditStates((prev) => ({
                            ...prev,
                            [qKey]: { ...prev[qKey], score: e.target.value },
                          }))
                        }
                        className="w-full bg-[#121316] border border-[#22242a] rounded px-2 py-1 text-xs text-[#ededed] focus:outline-none"
                      />
                    </div>

                    <div className="sm:col-span-2">
                      <label className="block text-[10px] text-[#717680] mb-0.5">Reason for Override</label>
                      <input
                        type="text"
                        value={editState.reason}
                        onChange={(e) =>
                          setEditStates((prev) => ({
                            ...prev,
                            [qKey]: { ...prev[qKey], reason: e.target.value },
                          }))
                        }
                        placeholder="Handwritten solution steps valid"
                        className="w-full bg-[#121316] border border-[#22242a] rounded px-2 py-1 text-xs text-[#ededed] focus:outline-none"
                      />
                    </div>
                  </div>

                  <div className="flex justify-end gap-2 pt-1">
                    <button
                      type="button"
                      onClick={() => toggleEditForm(q)}
                      className="px-2.5 py-0.5 rounded text-xs text-[#717680] hover:text-[#ededed]"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      disabled={isActing}
                      onClick={() => handleEditSubmit(q)}
                      className="px-3 py-0.5 rounded text-xs bg-[#2563eb] hover:bg-[#1d4ed8] text-white cursor-pointer"
                    >
                      Save Override
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
