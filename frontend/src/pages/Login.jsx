import React, { useState } from 'react';
import { ArrowRight, ClipboardCheck, Eye, EyeOff, FileUp, GraduationCap, NotebookPen, ShieldCheck } from 'lucide-react';
import api from '../api';
import { Button, errorText } from '../components/ui';

const PATH = [
  { icon: NotebookPen, title: 'Add the exam paper', text: 'AutoGrade reads the questions and marks. You approve them.' },
  { icon: FileUp, title: 'Upload answer sheets', text: 'Handwritten sheets are read one at a time, with live progress.' },
  { icon: ClipboardCheck, title: 'Check the draft marks', text: 'Anything uncertain is flagged for you, beside the scanned page.' },
  { icon: ShieldCheck, title: 'You decide what is final', text: 'A sheet only becomes final when a teacher approves it.' },
];

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
    <div className="grid min-h-dvh lg:grid-cols-[1.1fr_1fr]">
      <section className="hidden flex-col justify-center bg-primary-soft px-12 py-12 lg:flex xl:px-20">
        <div className="page-enter max-w-lg">
          <h2 className="text-4xl leading-[1.12] xl:text-[2.75rem]">Mark exam sheets faster. Stay in charge of every mark.</h2>
          <ol className="mt-10 space-y-6">
            {PATH.map(({ icon: Icon, title, text }, index) => (
              <li key={title} className="rise flex gap-4" style={{ '--i': index + 2 }}>
                <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-surface text-primary shadow-soft">
                  <Icon size={19} aria-hidden="true" />
                </span>
                <span>
                  <span className="block font-semibold">{title}</span>
                  <span className="block text-sm text-muted">{text}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="flex items-center justify-center px-5 py-12">
        <form onSubmit={handleSubmit} className="page-enter w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5">
            <span className="grid size-10 place-items-center rounded-xl bg-primary text-on-primary">
              <GraduationCap size={22} aria-hidden="true" />
            </span>
            <span className="font-display text-2xl font-semibold tracking-tight">AutoGrade</span>
          </div>

          <h1 className="text-[1.75rem]">Welcome back</h1>
          <p className="mt-1 text-muted">Enter the teacher password to continue.</p>

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
        </form>
      </section>
    </div>
  );
}
