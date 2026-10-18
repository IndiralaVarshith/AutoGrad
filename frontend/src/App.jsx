import React, { useState, useEffect } from 'react';
import { onAuthStateChanged, signOut } from 'firebase/auth';
import { auth } from './firebase';
import Login from './pages/Login';
import Setup from './pages/Setup';
import Upload from './pages/Upload';
import Dashboard from './pages/Dashboard';
import Review from './pages/Review';

export default function App() {
  const [user, setUser] = useState(null);
  const [currentPage, setCurrentPage] = useState('dashboard');
  const [currentExamId, setCurrentExamId] = useState('EXAM_2026_01');
  const [selectedHallTicket, setSelectedHallTicket] = useState(null);

  // Monitor Firebase Auth
  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (firebaseUser) => {
      if (firebaseUser) {
        setUser(firebaseUser);
      }
    });
    return () => unsubscribe();
  }, []);

  const handleLogout = async () => {
    try {
      await signOut(auth);
    } catch (e) {
      console.warn('Sign out notice:', e);
    }
    setUser(null);
    setCurrentPage('login');
  };

  // If not logged in, show Login
  if (!user && currentPage !== 'login') {
    return (
      <Login
        onLoginSuccess={(loggedInUser) => {
          setUser(loggedInUser);
          setCurrentPage('setup');
        }}
      />
    );
  }

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
          </div>

          {/* Clean Flat Navigation Tabs */}
          <nav className="flex items-center gap-1">
            <button
              onClick={() => setCurrentPage('setup')}
              className={`px-3 py-1.5 rounded text-xs font-mono tracking-wide transition-colors cursor-pointer ${
                currentPage === 'setup'
                  ? 'bg-[#1e2026] text-[#ffffff] font-medium border border-[#30333c]'
                  : 'text-[#858a94] hover:text-[#d1d5db] hover:bg-[#16171b]'
              }`}
            >
              Setup
            </button>

            <button
              onClick={() => setCurrentPage('upload')}
              className={`px-3 py-1.5 rounded text-xs font-mono tracking-wide transition-colors cursor-pointer ${
                currentPage === 'upload'
                  ? 'bg-[#1e2026] text-[#ffffff] font-medium border border-[#30333c]'
                  : 'text-[#858a94] hover:text-[#d1d5db] hover:bg-[#16171b]'
              }`}
            >
              Upload
            </button>

            <button
              onClick={() => setCurrentPage('dashboard')}
              className={`px-3 py-1.5 rounded text-xs font-mono tracking-wide transition-colors cursor-pointer ${
                currentPage === 'dashboard'
                  ? 'bg-[#1e2026] text-[#ffffff] font-medium border border-[#30333c]'
                  : 'text-[#858a94] hover:text-[#d1d5db] hover:bg-[#16171b]'
              }`}
            >
              Results
            </button>

            <button
              onClick={() => {
                setSelectedHallTicket(null);
                setCurrentPage('review');
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
              {user?.email || user?.displayName || 'Faculty'}
            </div>

            <button
              onClick={handleLogout}
              className="px-2.5 py-1 rounded bg-[#16171b] hover:bg-[#202227] text-[#858a94] hover:text-[#c4c7cf] border border-[#23262c] text-xs font-mono transition-colors cursor-pointer"
            >
              Sign Out
            </button>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 pb-10">
        {currentPage === 'setup' && (
          <Setup
            initialExamId={currentExamId}
            onExamApproved={(approvedExamId) => {
              setCurrentExamId(approvedExamId);
              setCurrentPage('upload');
            }}
          />
        )}

        {currentPage === 'upload' && (
          <Upload
            examId={currentExamId}
            onGoToDashboard={() => setCurrentPage('dashboard')}
          />
        )}

        {currentPage === 'dashboard' && (
          <Dashboard
            examId={currentExamId}
            onGoToReview={(hallTicket) => {
              setSelectedHallTicket(hallTicket);
              setCurrentPage('review');
            }}
          />
        )}

        {currentPage === 'review' && (
          <Review
            examId={currentExamId}
            initialHallTicket={selectedHallTicket}
            onBackToDashboard={() => setCurrentPage('dashboard')}
          />
        )}
      </main>

      {/* Minimalist Flat Footer */}
      <footer className="border-t border-[#1a1c20] bg-[#0c0d0e] py-3 text-center text-[11px] text-[#555963] font-mono">
        AutoGrade • Examination Assessment Engine
      </footer>
    </div>
  );
}
