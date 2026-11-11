import React, { useCallback, useEffect, useState } from 'react';
import { BarChart3, Check, ClipboardCheck, FileUp, GraduationCap, LogOut, Moon, NotebookPen, Sun } from 'lucide-react';
import api from './api';
import Login from './pages/Login';
import Setup from './pages/Setup';
import Upload from './pages/Upload';
import Dashboard from './pages/Dashboard';
import Review from './pages/Review';
import ExamSwitcher from './components/ExamSwitcher';
import { Tip, cx, useSlidingThumb } from './components/ui';

const STEPS = [
  { id: 'setup', label: 'Setup', hint: 'Add the exam paper and approve the marks', icon: NotebookPen },
  { id: 'upload', label: 'Upload', hint: "Add the students' answer sheets", icon: FileUp },
  { id: 'review', label: 'Review', hint: 'Check the answers that need a teacher', icon: ClipboardCheck },
  { id: 'dashboard', label: 'Results', hint: 'See the scores and export the spreadsheet', icon: BarChart3 },
];

const LOGO = (
  <span className="grid size-8 shrink-0 place-items-center rounded-[0.6rem] bg-primary text-on-primary">
    <GraduationCap size={18} aria-hidden="true" />
  </span>
);

function storedPage() {
  try {
    const saved = sessionStorage.getItem('autograde_current_page');
    return STEPS.some((step) => step.id === saved) ? saved : 'setup';
  } catch {
    return 'setup';
  }
}

function storedExam() {
  try {
    return sessionStorage.getItem('autograde_current_exam_id') || '';
  } catch {
    return '';
  }
}

function ThemeToggle() {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || 'light');

  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem('autograde_theme', next);
    } catch {
      // Private window: it just will not be remembered.
    }
  };

  return (
    <Tip text={theme === 'dark' ? 'Switch to light' : 'Switch to dark'} className="tip-down tip-end">
      <button type="button" onClick={toggle} className="icon-btn" aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}>
        <span key={theme} className="pop grid place-items-center">
          {theme === 'dark' ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
        </span>
      </button>
    </Tip>
  );
}

function StepNav({ currentPage, onChange, status }) {
  const { container, style } = useSlidingThumb(currentPage);

  return (
    <nav ref={container} aria-label="Steps" className="relative hidden items-center rounded-xl bg-sunken p-1 md:flex">
      <span className="thumb rounded-[0.6rem] bg-surface shadow-soft" style={style} aria-hidden="true" />
      {STEPS.map((step, index) => {
        const active = currentPage === step.id;
        const done = status[step.id]?.done;
        const count = status[step.id]?.count;
        return (
          <Tip key={step.id} text={step.hint} className="tip-down">
            <button
              type="button"
              data-active={active}
              aria-current={active ? 'step' : undefined}
              onClick={() => onChange(step.id)}
              className={cx(
                'relative flex items-center gap-2 rounded-[0.6rem] px-3 py-1.5 text-sm font-semibold transition-colors duration-200',
                active ? 'text-ink' : 'text-muted hover:text-ink',
              )}
            >
              <span
                className={cx(
                  'grid size-5 place-items-center rounded-full text-[11px] font-bold transition-colors duration-300',
                  done ? 'bg-primary text-on-primary' : active ? 'bg-ink text-paper' : 'bg-line text-muted',
                )}
              >
                {done ? <Check size={12} strokeWidth={3} aria-label="Done" /> : index + 1}
              </span>
              {step.label}
              {count > 0 && (
                <span className="rounded-full bg-warn-soft px-1.5 text-xs font-bold text-warn tabular-nums" aria-label={`${count} sheets need review`}>
                  {count}
                </span>
              )}
            </button>
          </Tip>
        );
      })}
    </nav>
  );
}

function MobileNav({ currentPage, onChange, status }) {
  return (
    <nav aria-label="Steps" className="fixed inset-x-0 bottom-0 z-50 grid grid-cols-4 border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden">
      {STEPS.map((step) => {
        const Icon = step.icon;
        const active = currentPage === step.id;
        const count = status[step.id]?.count;
        return (
          <button
            key={step.id}
            type="button"
            onClick={() => onChange(step.id)}
            aria-current={active ? 'step' : undefined}
            className={cx(
              'relative flex flex-col items-center gap-0.5 py-2.5 text-xs font-semibold transition-colors',
              active ? 'text-primary' : 'text-muted',
            )}
          >
            <Icon size={20} aria-hidden="true" />
            {step.label}
            {count > 0 && (
              <span className="absolute top-1.5 left-[calc(50%+6px)] rounded-full bg-warn-soft px-1.5 text-[11px] font-bold text-warn">{count}</span>
            )}
          </button>
        );
      })}
    </nav>
  );
}

export default function App() {
  const [signedIn, setSignedIn] = useState(null);
  const [currentPage, setCurrentPage] = useState(storedPage);
  const [currentExamId, setCurrentExamId] = useState(storedExam);
  const [openRequest, setOpenRequest] = useState(null);
  const [progress, setProgress] = useState({ rubric: false, sheets: 0, flagged: 0, final: 0 });
  const [examListVersion, setExamListVersion] = useState(0);

  // The server decides who is signed in, from its session cookie.
  useEffect(() => {
    api.authStatus()
      .then((status) => setSignedIn(status.signed_in))
      .catch(() => setSignedIn(false));
  }, []);

  const handlePageChange = useCallback((page) => {
    setCurrentPage(page);
    window.scrollTo({ top: 0 });
    try {
      sessionStorage.setItem('autograde_current_page', page);
    } catch {
      // ignore
    }
  }, []);

  const handleExamIdChange = useCallback((newExamId) => {
    const trimmed = (newExamId || '').trim();
    setCurrentExamId(trimmed);
    try {
      sessionStorage.setItem('autograde_current_exam_id', trimmed);
    } catch {
      // ignore
    }
  }, []);

  // Review and Results already hold fresh sheets, so the step counts follow them with no extra request.
  const handleResults = useCallback((papers) => {
    setProgress((now) => ({
      ...now,
      sheets: papers.length,
      flagged: papers.filter((paper) => paper.flagged_count > 0 && paper.finality_status !== 'FINAL').length,
      final: papers.filter((paper) => paper.finality_status === 'FINAL').length,
    }));
  }, []);

  // Shows where this exam has got to, as ticks and counts on the steps.
  useEffect(() => {
    if (!signedIn || !currentExamId) {
      setProgress({ rubric: false, sheets: 0, flagged: 0, final: 0 });
      return undefined;
    }
    let stopped = false;
    Promise.allSettled([api.getExamRubric(currentExamId), api.getExamResults(currentExamId)]).then(([rubric, results]) => {
      if (stopped) return;
      const papers = results.status === 'fulfilled' ? results.value?.results || [] : [];
      setProgress({
        rubric: rubric.status === 'fulfilled',
        sheets: papers.length,
        flagged: papers.filter((paper) => paper.flagged_count > 0 && paper.finality_status !== 'FINAL').length,
        final: papers.filter((paper) => paper.finality_status === 'FINAL').length,
      });
    });
    return () => {
      stopped = true;
    };
  }, [signedIn, currentExamId, currentPage]);

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch (e) {
      console.warn('Sign out notice:', e);
    }
    setSignedIn(false);
  };

  if (signedIn === null) {
    return (
      <div className="grid min-h-dvh place-items-center" aria-label="Loading">
        <span className="pulse-dot">{LOGO}</span>
      </div>
    );
  }
  if (!signedIn) return <Login onLoginSuccess={() => setSignedIn(true)} />;

  const status = {
    setup: { done: progress.rubric },
    upload: { done: progress.sheets > 0 },
    review: { done: progress.sheets > 0 && progress.final === progress.sheets, count: progress.flagged },
    dashboard: {},
  };

  const page = (id, content) => (
    <div hidden={currentPage !== id} className={currentPage === id ? 'page-enter' : undefined}>
      {content}
    </div>
  );

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-50 border-b border-line bg-surface/90 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-[90rem] items-center gap-3 px-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            {LOGO}
            <span className="hidden font-display text-lg font-semibold tracking-tight lg:inline">AutoGrade</span>
          </div>

          <ExamSwitcher value={currentExamId} onChange={handleExamIdChange} refreshKey={`${currentPage}:${examListVersion}`} />

          <div className="flex-1" />
          <StepNav currentPage={currentPage} onChange={handlePageChange} status={status} />
          <div className="flex-1" />

          <div className="flex items-center gap-1">
            <ThemeToggle />
            <Tip text="Sign out" className="tip-down tip-end">
              <button type="button" onClick={handleLogout} className="icon-btn" aria-label="Sign out">
                <LogOut size={18} aria-hidden="true" />
              </button>
            </Tip>
          </div>
        </div>
      </header>

      {/* Every page stays mounted, so half-finished work survives moving between steps. */}
      <main className="flex-1 pb-24 md:pb-12">
        {page('setup', (
          <Setup
            examId={currentExamId}
            onExamIdChange={handleExamIdChange}
            onExamApproved={(approvedExamId) => {
              handleExamIdChange(approvedExamId);
              setExamListVersion((version) => version + 1);
              handlePageChange('upload');
            }}
          />
        ))}
        {page('upload', (
          <Upload
            active={currentPage === 'upload'}
            examId={currentExamId}
            onGoToDashboard={() => handlePageChange('dashboard')}
            onGoToReview={() => handlePageChange('review')}
            onGoToSetup={() => handlePageChange('setup')}
          />
        ))}
        {page('review', (
          <Review
            examId={currentExamId}
            openRequest={openRequest}
            onResults={handleResults}
            active={currentPage === 'review'}
            onBackToDashboard={() => handlePageChange('dashboard')}
            onGoToUpload={() => handlePageChange('upload')}
          />
        ))}
        {page('dashboard', (
          <Dashboard
            examId={currentExamId}
            active={currentPage === 'dashboard'}
            onResults={handleResults}
            onGoToUpload={() => handlePageChange('upload')}
            onGoToReview={(hallTicket) => {
              setOpenRequest({ ticket: hallTicket });
              handlePageChange('review');
            }}
          />
        ))}
      </main>

      <MobileNav currentPage={currentPage} onChange={handlePageChange} status={status} />
    </div>
  );
}
