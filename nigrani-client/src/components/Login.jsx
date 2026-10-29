import { useState } from 'react';
import { Eye, EyeOff, LoaderCircle, Lock, ShieldCheck, User } from 'lucide-react';
import { describeError, login } from '../api/client.js';
import ErrorBanner from './ErrorBanner.jsx';

const DEMO_ACCOUNTS = [
  { username: 'sandbox', label: 'Digital Wallet' },
  { username: 'analyst', label: 'Analyst Monitor' },
];

export default function Login({ onLogin, notice }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(event) {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError('Enter a username and password.');
      return;
    }
    setLoading(true);
    setError('');
    try {
      onLogin(await login(username.trim(), password));
    } catch (err) {
      setError(err.response?.status === 401 ? 'Incorrect username or password.' : describeError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-950 px-4">
      {/* Ambient glow behind the glass card */}
      <div aria-hidden="true" className="pointer-events-none absolute -top-40 left-1/2 h-[480px] w-[480px] -translate-x-1/2 rounded-full bg-emerald-500/20 blur-3xl" />
      <div aria-hidden="true" className="pointer-events-none absolute -bottom-48 right-[15%] h-[420px] w-[420px] rounded-full bg-violet-600/20 blur-3xl" />

      <div className="relative w-full max-w-sm rounded-2xl border border-white/10 bg-white/[0.04] p-8 shadow-2xl shadow-black/50 backdrop-blur-xl">
        <div className="flex flex-col items-center text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-emerald-500/15 ring-1 ring-emerald-500/30">
            <ShieldCheck className="h-6 w-6 text-emerald-400" aria-hidden="true" />
          </div>
          <h1 className="mt-4 text-xl font-semibold tracking-tight text-slate-50">Sign in to Nigrani</h1>
          <p className="mt-1 text-sm text-slate-400">AI fraud command center · demo environment</p>
        </div>

        <form onSubmit={handleSubmit} className="mt-7 space-y-4" noValidate>
          {notice && !error && (
            <p className="rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs text-sky-200">{notice}</p>
          )}
          <ErrorBanner message={error} offline={error.startsWith('Cannot reach')} />

          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-300">Username</span>
            <div className="relative">
              <User className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" aria-hidden="true" />
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                placeholder="sandbox or analyst"
                className="w-full rounded-lg border border-white/10 bg-slate-950/60 py-2.5 pl-9 pr-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-emerald-500/60 focus:outline-none"
              />
            </div>
          </label>

          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-slate-300">Password</span>
            <div className="relative">
              <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" aria-hidden="true" />
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="w-full rounded-lg border border-white/10 bg-slate-950/60 py-2.5 pl-9 pr-10 text-sm text-slate-100 focus:border-emerald-500/60 focus:outline-none"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-slate-500 hover:text-slate-300"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </label>

          <button
            type="submit"
            disabled={loading}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-500 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading && <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {loading ? 'Authenticating…' : 'Sign in'}
          </button>
        </form>

        <div className="mt-6 border-t border-white/10 pt-4">
          <p className="text-center text-[11px] uppercase tracking-wider text-slate-500">Demo accounts · password <span className="font-mono normal-case text-slate-400">demo</span></p>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {DEMO_ACCOUNTS.map((acct) => (
              <button
                key={acct.username}
                type="button"
                onClick={() => { setUsername(acct.username); setPassword('demo'); setError(''); }}
                className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-2 text-left transition hover:border-emerald-500/40 hover:bg-emerald-500/5"
              >
                <span className="block font-mono text-xs text-slate-200">{acct.username}</span>
                <span className="block text-[11px] text-slate-500">{acct.label}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
