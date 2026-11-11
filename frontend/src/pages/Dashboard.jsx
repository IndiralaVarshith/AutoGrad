import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowRight, ArrowUp, BadgeCheck, ChevronDown, ChevronsUpDown, Clock, Flag, Inbox, RefreshCw, Search } from 'lucide-react';
import api from '../api';
import ConfidenceBadge from '../components/ConfidenceBadge';
import EvidenceTable from '../components/EvidenceTable';
import ExportButton from '../components/ExportButton';
import {
  Button, CountUp, EmptyState, Expand, PageHeader, Pill, Segmented, Skeleton, Tip, cx,
} from '../components/ui';

/** A flag always wins: a sheet with undecided answers never reads as ready or final. */
function sheetState(paper) {
  if (paper.flagged_count > 0) return 'flagged';
  if (paper.finality_status === 'FINAL') return 'final';
  if (paper.status === 'NEEDS_REVIEW') return 'flagged';
  return 'ready';
}

function StateBadge({ paper }) {
  const state = sheetState(paper);
  if (state === 'flagged') {
    return <Pill tone="warn" icon={Flag}>{paper.flagged_count > 0 ? `${paper.flagged_count} to check` : 'Needs review'}</Pill>;
  }
  if (state === 'final') return <Pill tone="good" icon={BadgeCheck}>Final</Pill>;
  return <Pill icon={Clock}>Not approved yet</Pill>;
}

function StatTile({ label, value, decimals = 0, sub, icon: Icon, tone }) {
  return (
    <div className="card rise p-4">
      <div className="flex items-center gap-1.5 text-[13px] text-muted">
        {Icon && <Icon size={14} className={tone} aria-hidden="true" />}
        {label}
      </div>
      <div className="mt-1 font-sans text-[1.75rem] leading-tight font-semibold">
        <CountUp value={value} decimals={decimals} />
        {sub && <span className="ml-1 text-sm font-normal text-muted">{sub}</span>}
      </div>
    </div>
  );
}

const BANDS = Array.from({ length: 10 }, (_, i) => i);

function ScoreSpread({ results }) {
  const counts = useMemo(() => {
    const bins = BANDS.map(() => 0);
    results.forEach((paper) => {
      if (!paper.max_total) return;
      const share = (paper.total_score || 0) / paper.max_total;
      bins[Math.min(9, Math.max(0, Math.floor(share * 10)))] += 1;
    });
    return bins;
  }, [results]);
  const tallest = Math.max(1, ...counts);
  const counted = counts.reduce((sum, count) => sum + count, 0);
  if (counted < 2) return null;

  return (
    <section className="card rise p-5" aria-label="Spread of scores">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg">Spread of scores</h2>
        <p className="text-[13px] text-muted">Marks not decided yet do not count, so scores can still rise.</p>
      </div>
      <div className="mt-4 flex h-28 items-end gap-0.5 border-b border-line-strong">
        {counts.map((count, band) => {
          const range = `${band * 10}–${band === 9 ? 100 : band * 10 + 10}%`;
          const summary = `${range}: ${count} ${count === 1 ? 'sheet' : 'sheets'}`;
          return (
            <button
              key={band}
              type="button"
              aria-label={summary}
              className="tip group flex h-full min-w-0 flex-1 cursor-default flex-col items-stretch justify-end rounded-t-sm transition-colors hover:bg-sunken focus-visible:bg-sunken"
            >
              {count > 0 && (
                <>
                  <span className="fade mb-0.5 text-center text-xs font-semibold tabular-nums">{count}</span>
                  <span
                    className="bar-grow block rounded-t-[4px] transition-opacity group-hover:opacity-80"
                    style={{ '--i': band, height: `${(count / tallest) * 78}%`, background: 'var(--chart)' }}
                  />
                </>
              )}
              <span role="tooltip" className="tip-body">{summary}</span>
            </button>
          );
        })}
      </div>
      <div className="mt-1.5 flex justify-between text-xs text-muted tabular-nums" aria-hidden="true">
        <span>0%</span>
        <span>50%</span>
        <span>100%</span>
      </div>
    </section>
  );
}

function SortHeader({ label, sortKey, sort, onSort, align = 'left' }) {
  const active = sort.key === sortKey;
  const Icon = !active ? ChevronsUpDown : sort.direction === 'asc' ? ArrowUp : ArrowDown;
  return (
    <th scope="col" className={cx('px-4 py-2.5 font-semibold', align === 'right' && 'text-right')} aria-sort={active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" onClick={() => onSort(sortKey)} className="inline-flex items-center gap-1 rounded transition-colors hover:text-ink">
        {label}
        <Icon size={13} aria-hidden="true" className={active ? 'text-ink' : 'opacity-60'} />
      </button>
    </th>
  );
}

function ResultRow({ paper, index, onGoToReview }) {
  const [open, setOpen] = useState(false);
  const [hasOpened, setHasOpened] = useState(false);
  const toggle = () => {
    setHasOpened(true);
    setOpen(!open);
  };
  const flagged = sheetState(paper) === 'flagged';
  const share = paper.max_total ? Math.min(100, ((paper.total_score || 0) / paper.max_total) * 100) : 0;

  return (
    <>
      <tr
        onClick={toggle}
        className={cx('rise cursor-pointer border-t border-line transition-colors duration-150 hover:bg-sunken/70', open && 'bg-sunken/70')}
        style={{ '--i': Math.min(index, 10) }}
      >
        <td className="py-3 pr-1 pl-3">
          <button
            type="button"
            aria-expanded={open}
            aria-label={`${open ? 'Hide' : 'Show'} questions for ${paper.hall_ticket}`}
            onClick={(e) => {
              e.stopPropagation();
              toggle();
            }}
            className="icon-btn size-7"
          >
            <ChevronDown size={16} aria-hidden="true" className={cx('transition-transform duration-300', open ? 'rotate-0' : '-rotate-90')} />
          </button>
        </td>
        <td className="px-4 py-3 font-semibold">{paper.hall_ticket}</td>
        <td className="px-4 py-3 text-muted">{paper.student_name || 'Not given'}</td>
        <td className="px-4 py-3">
          <div className="flex items-center gap-3">
            <span className="w-16 whitespace-nowrap tabular-nums">
              <strong>{paper.total_score}</strong>
              <span className="text-muted"> / {paper.max_total}</span>
            </span>
            <span className="hidden h-1.5 w-20 overflow-hidden rounded-full bg-sunken sm:block" aria-hidden="true">
              <span className="block h-full w-full origin-left rounded-full transition-transform duration-500" style={{ transform: `scaleX(${share / 100})`, background: 'var(--chart)' }} />
            </span>
          </div>
        </td>
        <td className="px-4 py-3"><StateBadge paper={paper} /></td>
        <td className="px-4 py-3 text-right" onClick={(e) => e.stopPropagation()}>
          <Button size="sm" variant={flagged ? 'primary' : 'ghost'} onClick={() => onGoToReview?.(paper.hall_ticket)}>
            {flagged ? 'Review' : 'Open'}
            <ArrowRight size={14} aria-hidden="true" />
          </Button>
        </td>
      </tr>
      {hasOpened && (
        <tr>
          <td colSpan={6} className="p-0">
            <Expand open={open}>
              <div className="space-y-2.5 bg-sunken/70 px-4 pt-1 pb-4">
                {(paper.questions || []).map((q) => (
                  <div key={`${q.question_number}:${q.sub_part || ''}`} className="lazy-card rounded-xl border border-line bg-surface p-3.5">
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold">Q{q.question_number}{q.sub_part || ''}</span>
                        {q.question_type && <Pill>{q.question_type}</Pill>}
                        <ConfidenceBadge routing={q.routing} score={q.c_final} />
                      </div>
                      <span className="text-sm tabular-nums"><strong>{q.final_score}</strong> <span className="text-muted">of {q.max_marks}</span></span>
                    </div>
                    <EvidenceTable criteria={q.criteria} />
                  </div>
                ))}
              </div>
            </Expand>
          </td>
        </tr>
      )}
    </>
  );
}

export default function Dashboard({ examId = '', onGoToReview, onGoToUpload, onResults, active = true }) {
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterStatus, setFilterStatus] = useState('ALL');
  const [sort, setSort] = useState({ key: null, direction: 'asc' });
  const lastPayload = useRef('');
  const examRef = useRef(examId);
  examRef.current = examId;

  const fetchResults = useCallback(async () => {
    if (!examId) {
      setResults([]);
      setLoading(false);
      return;
    }
    try {
      const data = await api.getExamResults(examId);
      if (examRef.current !== examId || !Array.isArray(data?.results)) return;
      // Nothing changed means nothing redraws, so polling never disturbs the page.
      const payload = JSON.stringify(data.results);
      if (payload !== lastPayload.current) {
        lastPayload.current = payload;
        setResults(data.results);
        onResults?.(data.results);
      }
    } catch (err) {
      console.warn('API fetch notice:', err.message);
    } finally {
      setLoading(false);
    }
  }, [examId, onResults]);

  useEffect(() => {
    lastPayload.current = '';
    setResults([]);
    setLoading(true);
  }, [examId]);

  useEffect(() => {
    if (!active) return undefined;
    fetchResults();
    const pollInterval = setInterval(fetchResults, 5000);
    return () => clearInterval(pollInterval);
  }, [fetchResults, active]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await fetchResults();
    setRefreshing(false);
  };

  const handleSort = (key) => {
    setSort((current) => {
      if (current.key !== key) return { key, direction: key === 'total_score' ? 'desc' : 'asc' };
      return { key, direction: current.direction === 'asc' ? 'desc' : 'asc' };
    });
  };

  const counts = useMemo(() => {
    const tally = { flagged: 0, ready: 0, final: 0 };
    results.forEach((paper) => { tally[sheetState(paper)] += 1; });
    return tally;
  }, [results]);

  const shown = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const list = results.filter((r) => {
      const matchesSearch = (r.hall_ticket || '').toLowerCase().includes(q) || (r.student_name || '').toLowerCase().includes(q);
      return matchesSearch && (filterStatus === 'ALL' || sheetState(r) === filterStatus);
    });
    if (!sort.key) return list;
    const sign = sort.direction === 'asc' ? 1 : -1;
    return [...list].sort((a, b) => {
      if (sort.key === 'total_score') return sign * ((a.total_score || 0) - (b.total_score || 0));
      return sign * String(a[sort.key] || '').localeCompare(String(b[sort.key] || ''), undefined, { numeric: true });
    });
  }, [results, searchQuery, filterStatus, sort]);

  const totalPapers = results.length;
  const avgScore = totalPapers > 0 ? results.reduce((acc, r) => acc + (r.total_score || 0), 0) / totalPapers : 0;
  const maxTotal = results[0]?.max_total;

  const header = (
    <PageHeader
      eyebrow="Step 4 of 4"
      title="Results"
      subtitle={examId ? `Scores for ${examId}. Only sheets a teacher has approved are final.` : 'Scores for one exam at a time.'}
      actions={(
        <>
          <Button variant="ghost" icon={RefreshCw} loading={refreshing} disabled={!examId} onClick={handleRefresh}>Refresh</Button>
          <ExportButton examId={examId} disabled={results.length === 0} />
        </>
      )}
    />
  );

  if (!loading && totalPapers === 0) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        {header}
        <EmptyState
          icon={Inbox}
          title={examId ? 'No results yet' : 'Choose an exam first'}
          action={<Button variant="primary" onClick={onGoToUpload}>Add answer sheets</Button>}
        >
          {examId ? `Sheets for ${examId} appear here as soon as they are marked.` : 'Use the Exam ID menu at the top of the page.'}
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-4 py-8 sm:px-6">
      {header}

      {loading ? (
        <div className="space-y-4" aria-label="Loading results">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[0, 1, 2, 3].map((n) => <Skeleton key={n} className="h-24 rounded-2xl" />)}
          </div>
          <Skeleton className="h-72 rounded-2xl" />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Sheets marked" value={totalPapers} />
            <StatTile label="Need your review" value={counts.flagged} icon={Flag} tone="text-warn" />
            <StatTile label="Final" value={counts.final} icon={BadgeCheck} tone="text-good" sub={`of ${totalPapers}`} />
            <StatTile label="Average so far" value={avgScore} decimals={1} sub={maxTotal ? `of ${maxTotal}` : undefined} />
          </div>

          <ScoreSpread results={results} />

          <div className="card overflow-hidden">
            <div className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative w-full sm:w-72">
                <Search size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" aria-hidden="true" />
                <input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search roll number or name"
                  aria-label="Search sheets"
                  className="input min-h-9 py-1.5 pl-9 text-sm"
                />
              </div>
              <div className="max-w-full overflow-x-auto">
                <Segmented
                  label="Filter sheets"
                  value={filterStatus}
                  onChange={setFilterStatus}
                  options={[
                    { value: 'ALL', label: `All ${totalPapers}` },
                    { value: 'flagged', label: `Need review ${counts.flagged}` },
                    { value: 'ready', label: `Not approved ${counts.ready}` },
                    { value: 'final', label: `Final ${counts.final}` },
                  ]}
                />
              </div>
            </div>

            {shown.length === 0 ? (
              <p className="border-t border-line px-4 py-12 text-center text-sm text-muted">No sheets match. Try clearing the search or the filter.</p>
            ) : (
              <div className="relative overflow-x-auto">
                <table className="w-full min-w-[42rem] border-collapse text-left text-sm">
                  <thead>
                    <tr className="border-t border-line text-[13px] text-muted">
                      <th scope="col" className="w-10"><span className="sr-only">Details</span></th>
                      <SortHeader label="Roll number" sortKey="hall_ticket" sort={sort} onSort={handleSort} />
                      <SortHeader label="Student" sortKey="student_name" sort={sort} onSort={handleSort} />
                      <SortHeader label="Score" sortKey="total_score" sort={sort} onSort={handleSort} />
                      <th scope="col" className="px-4 py-2.5 font-semibold">Status</th>
                      <th scope="col" className="px-4 py-2.5"><span className="sr-only">Action</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((paper, index) => (
                      <ResultRow key={paper.hall_ticket} paper={paper} index={index} onGoToReview={onGoToReview} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <p className="flex justify-end text-[13px] text-muted">
            <Tip text="Marks with no flag can still be wrong. Spot-check some before approving them.">
              <span tabIndex={0} className="cursor-help underline decoration-dotted underline-offset-2">Can I trust unflagged marks?</span>
            </Tip>
          </p>
        </>
      )}
    </div>
  );
}
