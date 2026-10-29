import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { LoaderCircle, LogOut, ShieldCheck } from 'lucide-react';
import { AUTH_EXPIRED_EVENT, clearSession, getSession } from './api/client.js';
import Login from './components/Login.jsx';

// Each role only downloads its own view (Recharts ships with the analyst bundle)
const AnalystMonitor = lazy(() => import('./components/AnalystMonitor.jsx'));
const WalletSandbox = lazy(() => import('./components/WalletSandbox.jsx'));

const ROLE_BADGES = {
  admin: { label: 'Analyst · Admin', className: 'bg-violet-500/10 text-violet-300 ring-violet-500/30' },
  wallet: { label: 'Wallet · Sandbox', className: 'bg-sky-500/10 text-sky-300 ring-sky-500/30' },
  sandbox: { label: 'Wallet · Sandbox', className: 'bg-sky-500/10 text-sky-300 ring-sky-500/30' },
};

export default function App() {
  const [session, setSession] = useState(null);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState('');

  // Restore an active session from localStorage on mount
  useEffect(() => {
    setSession(getSession());
    setReady(true);
  }, []);

  // The API client fires this on any 401: drop the session and show login
  useEffect(() => {
    const onExpired = () => {
      setSession(null);
      setNotice('Your session expired. Please sign in again.');
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
  }, []);

  const handleLogin = useCallback((next) => {
    setNotice('');
    setSession(next);
  }, []);

  const handleLogout = useCallback(() => {
    clearSession();
    setSession(null);
  }, []);

  if (!ready) return null;

  const role = session?.role;
  const isAnalyst = role === 'admin';
  const isWallet = role === 'wallet' || role === 'sandbox';

  if (!session || (!isAnalyst && !isWallet)) {
    return <Login onLogin={handleLogin} notice={notice} />;
  }

  const badge = ROLE_BADGES[role];
  return (
    <div className="min-h-screen bg-slate-950">
      <header className="sticky top-0 z-30 border-b border-slate-800 bg-slate-950/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/15 ring-1 ring-emerald-500/30">
              <ShieldCheck className="h-4.5 w-4.5 text-emerald-400" aria-hidden="true" />
            </div>
            <span className="text-[15px] font-semibold tracking-tight text-slate-100">Nigrani</span>
            <span className="hidden text-xs text-slate-500 sm:inline">Fraud Command Center</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden font-mono text-xs text-slate-400 sm:inline">{session.username}</span>
            <span className={`rounded-md px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${badge.className}`}>
              {badge.label}
            </span>
            <button
              type="button"
              onClick={handleLogout}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition hover:border-red-500/50 hover:bg-red-500/10 hover:text-red-300"
            >
              <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
              Disconnect
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">
        <Suspense
          fallback={
            <div className="flex h-64 items-center justify-center text-sm text-slate-500">
              <LoaderCircle className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" /> Loading workspace…
            </div>
          }
        >
          {isAnalyst ? <AnalystMonitor /> : <WalletSandbox />}
        </Suspense>
      </main>
    </div>
  );
}
