import React, { useState, useEffect } from 'react';
import api from './api';
import Login from './pages/Login';
import Setup from './pages/Setup';
import Upload from './pages/Upload';
import Dashboard from './pages/Dashboard';
import Review from './pages/Review';

export default function App() {
  const [signedIn, setSignedIn] = useState(null);
  const [currentPage, setCurrentPage] = useState(() => {
    return sessionStorage.getItem('autograde_current_page') || 'setup';
  });
  const [currentExamId, setCurrentExamId] = useState(() => {
    return sessionStorage.getItem('autograde_current_exam_id') || '';
  });
  const [selectedHallTicket, setSelectedHallTicket] = useState(null);

  // The server decides who is signed in, from its session cookie.
  useEffect(() => {
    api.authStatus()
      .then((status) => setSignedIn(status.signed_in))
      .catch(() => setSignedIn(false));
  }, []);

  const handlePageChange = (page) => {
    setCurrentPage(page);
    try {
      sessionStorage.setItem('autograde_current_page', page);
    } catch (e) {
      // ignore
    }
  };

  const handleExamIdChange = (newExamId) => {
    if (newExamId && typeof newExamId === 'string') {
      const trimmed = newExamId.trim();
      setCurrentExamId(trimmed);
      try {
        sessionStorage.setItem('autograde_current_exam_id', trimmed);
      } catch (e) {
        // ignore
      }
    }
  };

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch (e) {
      console.warn('Sign out notice:', e);
    }
    setSignedIn(false);
  };

  if (signedIn === null) return null;
  if (!signedIn) return <Login onLoginSuccess={() => setSignedIn(true)} />;

  return (
    <div className="min-h-screen bg-[#0c0d0e] text-[#ededed] flex flex-col font-sans">
      {/* Top Header */}
      <header className="sticky top-0 z-50 bg-[#111215] border-b border-[#212329]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-13 flex items-center justify-between">
          {/* Plain Text Title */}
          <div className="flex items-center gap-3">
            <span className="text-sm font-semibold tracking-tight text-[#f0f2f5]">
              AutoGrade
            </span>
            <span className="hidden sm:inline-block text-[#383c44]">|</span>
            <span className="hidden sm:inline-block font-mono text-[11px] text-[#717680] uppercase tracking-wider">
              Evaluation Workbench
            </span>
            {currentExamId && (
              <span className="hidden md:inline-block font-mono text-[11px] text-[#3b82f6] bg-[#0f172a] border border-[#1e293b] px-2 py-0.5 rounded">
                Exam: {currentExamId}
              </span>
            )}
          </div>

          {/* Clean Flat Navigation Tabs */}
          <nav className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => handlePageChange('setup')}
              className={`px-3 py-1.5 rounded text-xs font-mono tracking-wide transition-colors cursor-pointer ${
                currentPage === 'setup'
                  ? 'bg-[#1e2026] text-[#ffffff] font-medium border border-[#30333c]'
                  : 'text-[#858a94] hover:text-[#d1d5db] hover:bg-[#16171b]'
              }`}
            >
              Setup
            </button>

            <button
              type="button"
              onClick={() => handlePageChange('upload')}
              className={`px-3 py-1.5 rounded text-xs font-mono tracking-wide transition-colors cursor-pointer ${
                currentPage === 'upload'
                  ? 'bg-[#1e2026] text-[#ffffff] font-medium border border-[#30333c]'
                  : 'text-[#858a94] hover:text-[#d1d5db] hover:bg-[#16171b]'
              }`}
            >
              Upload
            </button>

            <button
              type="button"
              onClick={() => handlePageChange('dashboard')}
              className={`px-3 py-1.5 rounded text-xs font-mono tracking-wide transition-colors cursor-pointer ${
                currentPage === 'dashboard'
                  ? 'bg-[#1e2026] text-[#ffffff] font-medium border border-[#30333c]'
                  : 'text-[#858a94] hover:text-[#d1d5db] hover:bg-[#16171b]'
              }`}
            >
              Results
            </button>

            <button
              type="button"
              onClick={() => {
                setSelectedHallTicket(null);
                handlePageChange('review');
              }}
              className={`px-3 py-1.5 rounded text-xs font-mono tracking-wide transition-colors cursor-pointer ${
                currentPage === 'review'
                  ? 'bg-[#1e2026] text-[#f59e0b] font-medium border border-[#3c2a17]'
                  : 'text-[#858a94] hover:text-[#d1d5db] hover:bg-[#16171b]'
              }`}
            >
              Review
            </button>
          </nav>

          {/* User Account Info */}
          <div className="flex items-center gap-3">
            <div className="hidden sm:block text-right font-mono text-[11px] text-[#717680]">
              Teacher
            </div>

            <button
              type="button"
              onClick={handleLogout}
              className="px-2.5 py-1 rounded bg-[#16171b] hover:bg-[#202227] text-[#858a94] hover:text-[#c4c7cf] border border-[#23262c] text-xs font-mono transition-colors cursor-pointer"
            >
              Sign Out
            </button>
          </div>
        </div>
      </header>

      {/* Main Content Area — Persistent mounting with CSS display control */}
      <main className="flex-1 pb-10">
        <div style={{ display: currentPage === 'setup' ? 'block' : 'none' }}>
          <Setup
            initialExamId={currentExamId}
            onExamIdChange={handleExamIdChange}
            onExamApproved={(approvedExamId) => {
              handleExamIdChange(approvedExamId);
              handlePageChange('upload');
            }}
          />
        </div>

        <div style={{ display: currentPage === 'upload' ? 'block' : 'none' }}>
          <Upload
            active={currentPage === 'upload'}
            examId={currentExamId}
            onExamIdChange={handleExamIdChange}
            onGoToDashboard={() => handlePageChange('dashboard')}
          />
        </div>

        <div style={{ display: currentPage === 'dashboard' ? 'block' : 'none' }}>
          <Dashboard
            examId={currentExamId}
            onGoToReview={(hallTicket) => {
              setSelectedHallTicket(hallTicket);
              handlePageChange('review');
            }}
          />
        </div>

        <div style={{ display: currentPage === 'review' ? 'block' : 'none' }}>
          <Review
            examId={currentExamId}
            initialHallTicket={selectedHallTicket}
            active={currentPage === 'review'}
            onBackToDashboard={() => handlePageChange('dashboard')}
          />
        </div>
      </main>

      {/* Minimalist Flat Footer */}
      <footer className="border-t border-[#1a1c20] bg-[#0c0d0e] py-3 text-center text-[11px] text-[#555963] font-mono">
        AutoGrade • Examination Assessment Engine
      </footer>
    </div>
  );
}
