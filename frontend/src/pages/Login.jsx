import React, { useState } from 'react';
import { ArrowRight, Check, Eye, EyeOff, GraduationCap } from 'lucide-react';
import api from '../api';
import { Button, errorText } from '../components/ui';

const STEPS = [
  'Add the question paper and approve its marks',
  'Upload the answer sheets: one Gemini request each',
  'Check the flagged answers beside the scanned page',
  'Optional: calibrate with one sheet you marked',
  'Approve. Only then is a sheet final',
];

/** Every start of the app begins here: the left page explains, the right page signs in. */
export default function Login({ onLoginSuccess }) {
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  const handleSubmit = async (event) => {
    event.preventDefault();
    try {
      setLoading(true);
      setError('');
      await api.login(password);
      onLoginSuccess();
    } catch (err) {
      setError(err.response ? errorText(err) : 'Could not reach the server. Is the backend running?');
      setAttempt((value) => value + 1);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid min-h-dvh place-items-center px-4 py-10">
      <div className="notebook page-enter w-full max-w-4xl">
        <section className="notebook-page notebook-left hidden md:block">
          <p className="text-[13px] font-semibold tracking-wide text-primary uppercase">How it works</p>
          <h2 className="mt-2 text-[2.1rem] leading-tight">
            Mark faster. <span className="highlight">Stay in charge</span> of every mark.
          </h2>
          <ol className="mt-7 space-y-3.5">
            {STEPS.map((step, index) => (
              <li key={step} className="rise flex items-start gap-3" style={{ '--i': index + 2 }}>
                <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-md border-2 border-primary text-primary">
                  <Check size={14} strokeWidth={3} aria-hidden="true" />
                </span>
                <span className="font-display text-lg leading-snug">{step}</span>
              </li>
            ))}
          </ol>
        </section>

        <div className="notebook-spine hidden md:block" aria-hidden="true" />

        <section className="notebook-page notebook-right">
          <form onSubmit={handleSubmit} className="mx-auto w-full max-w-sm">
            <div className="mb-8 flex items-center gap-3">
              <span className="logo-badge grid size-11 place-items-center text-on-primary">
                <GraduationCap size={23} aria-hidden="true" />
              </span>
              <span className="font-display text-3xl leading-none">AutoGrade</span>
            </div>

            <h1 className="text-[2rem]">Welcome back</h1>
            <p className="mt-1 text-muted">Enter the teacher password to open the notebook.</p>

            <div key={attempt} className={attempt > 0 ? 'shake mt-6' : 'mt-6'}>
              <label htmlFor="password" className="label">Password</label>
              <div className="relative">
                <input
                  id="password"
                  type={show ? 'text' : 'password'}
                  autoComplete="current-password"
                  autoFocus
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  aria-invalid={Boolean(error)}
                  aria-describedby={error ? 'password-error' : undefined}
                  className="input pr-11"
                />
                <button
                  type="button"
                  onClick={() => setShow(!show)}
                  aria-label={show ? 'Hide password' : 'Show password'}
                  aria-pressed={show}
                  className="icon-btn absolute top-0.5 right-0.5"
                >
                  {show ? <EyeOff size={17} aria-hidden="true" /> : <Eye size={17} aria-hidden="true" />}
                </button>
              </div>
              {error && <p id="password-error" role="alert" className="fade mt-2 text-sm font-medium text-bad">{error}</p>}
            </div>

            <Button type="submit" variant="primary" size="lg" loading={loading} className="mt-5 w-full">
              {loading ? 'Signing in' : 'Sign in'}
              {!loading && <ArrowRight size={17} aria-hidden="true" />}
            </Button>
            <p className="mt-4 text-center text-[13px] text-muted">
              You sign in again each time the app starts.
            </p>
          </form>
        </section>
      </div>
    </div>
  );
}
