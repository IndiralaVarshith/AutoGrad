import React, { useState } from 'react';
import api from '../api';

export default function Login({ onLoginSuccess }) {
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (event) => {
    event.preventDefault();
    try {
      setLoading(true);
      setError('');
      await api.login(password);
      onLoginSuccess();
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not reach the server. Is the backend running?');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0c0d0e] flex items-center justify-center px-4">
      <form onSubmit={handleSubmit} className="w-full max-w-sm space-y-4 rounded border border-[#22242a] bg-[#121316] p-6">
        <div>
          <h1 className="text-sm font-semibold text-[#f0f2f5]">AutoGrade</h1>
          <p className="mt-1 text-xs text-[#8c9099]">Enter the teacher password.</p>
        </div>
        <label className="block text-xs text-[#8c9099]">
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded border border-[#2e323b] bg-[#0c0d0e] p-2 text-sm text-[#e6e8ec]"
          />
        </label>
        {error && <p className="text-xs text-[#ef4444]">{error}</p>}
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded bg-[#2563eb] py-2 text-xs font-medium text-white cursor-pointer disabled:opacity-50"
        >
          {loading ? 'Signing in...' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
