import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, Bot, Inbox, Network, Pause, Play, Radar, RefreshCw, ScanSearch } from 'lucide-react';
import client, { describeError } from '../api/client.js';
import { AGENT_LABELS, formatNPR, formatPct, formatTime, isMuleFlag } from '../lib/format.js';
import DecisionBadge from './DecisionBadge.jsx';
import ErrorBanner from './ErrorBanner.jsx';
import MuleGraphModal from './MuleGraphModal.jsx';
import ShapBreakdown from './ShapBreakdown.jsx';

const POLL_MS = 2000;

export default function AnalystMonitor() {
  const [transactions, setTransactions] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [lastUpdated, setLastUpdated] = useState(null);
  const [paused, setPaused] = useState(false);
  const [selectedRef, setSelectedRef] = useState(null);
  const [graphOpen, setGraphOpen] = useState(false);
  const [clearState, setClearState] = useState({ status: 'idle', message: '' });
  const inFlight = useRef(false);

  const fetchFeed = useCallback(async (signal) => {
    if (inFlight.current) return; // a slow response never stacks requests
    inFlight.current = true;
    try {
      const { data } = await client.get('/live-transactions', { signal });
      setTransactions(Array.isArray(data) ? data : []);
      setLastUpdated(new Date());
      setError('');
    } catch (err) {
      if (err.code === 'ERR_CANCELED') return;
      // Keep showing the last good snapshot; just surface the problem
      setError(describeError(err));
    } finally {
      inFlight.current = false;
      setLoaded(true);
    }
  }, []);

  // Polling engine: fetch immediately, then every POLL_MS while not paused
  useEffect(() => {
    if (paused) return undefined;
    const controller = new AbortController();
    fetchFeed(controller.signal);
    const id = setInterval(() => fetchFeed(controller.signal), POLL_MS);
    return () => {
      clearInterval(id);
      controller.abort();
      inFlight.current = false;
    };
  }, [paused, fetchFeed]);

  // Selection is by transaction_ref, not row index: the feed re-sorts every poll
  const selected = useMemo(
    () => transactions.find((t) => t.transaction_ref === selectedRef) ?? null,
    [transactions, selectedRef],
  );
  const agedOut = selectedRef !== null && selected === null && loaded;

  const stats = useMemo(() => {
    const count = (fn) => transactions.filter(fn).length;
    return {
      total: transactions.length,
      blocked: count((t) => t.decision === 'BLOCK'),
      stepUp: count((t) => t.decision === 'OTP' || t.decision === 'DUAL_OTP'),
      overrides: count((t) => t.decision !== t.ml_decision),
    };
  }, [transactions]);

  async function clearVelocityCache() {
    setClearState({ status: 'pending', message: '' });
    try {
      const { data } = await client.post('/clear-cache');
      setClearState({ status: 'done', message: data?.message ?? 'Velocity cache cleared' });
    } catch (err) {
      setClearState({ status: 'error', message: describeError(err) });
    }
    setTimeout(() => setClearState({ status: 'idle', message: '' }), 4000);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-slate-50">
            <Radar className="h-5 w-5 text-emerald-400" aria-hidden="true" /> Analyst Command Center
          </h1>
          <p className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
            <span className={`inline-block h-2 w-2 rounded-full ${paused ? 'bg-slate-500' : error ? 'bg-red-500' : 'animate-pulse bg-emerald-400'}`} aria-hidden="true" />
            {paused ? 'Feed paused' : error ? 'Reconnecting…' : `Live · polling every ${POLL_MS / 1000}s`}
            {lastUpdated && <span className="font-mono">· updated {formatTime(lastUpdated.toISOString())}</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {clearState.message && (
            <span className={`text-xs ${clearState.status === 'error' ? 'text-red-300' : 'text-emerald-300'}`}>{clearState.message}</span>
          )}
          <button
            type="button"
            onClick={clearVelocityCache}
            disabled={clearState.status === 'pending'}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${clearState.status === 'pending' ? 'animate-spin' : ''}`} aria-hidden="true" />
            Clear velocity cache
          </button>
          <button
            type="button"
            onClick={() => setPaused((p) => !p)}
            aria-pressed={paused}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              paused
                ? 'bg-emerald-500 text-slate-950 hover:bg-emerald-400'
                : 'border border-slate-700 text-slate-200 hover:bg-slate-800'
            }`}
          >
            {paused ? <Play className="h-3.5 w-3.5" aria-hidden="true" /> : <Pause className="h-3.5 w-3.5" aria-hidden="true" />}
            {paused ? 'Resume Live Feed' : 'Pause Live Feed'}
          </button>
        </div>
      </div>

      <ErrorBanner message={error && `Live feed interrupted — showing last known data. ${error}`} offline={error.startsWith('Cannot reach')} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Transactions" value={stats.total} icon={Activity} />
        <Kpi label="Blocked" value={stats.blocked} tone="text-red-300" />
        <Kpi label="Step-up (OTP / Dual)" value={stats.stepUp} tone="text-amber-300" />
        <Kpi label="Agent overrides" value={stats.overrides} icon={Bot} hint="Final decision stricter than the ML model" />
      </div>

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
        <TransactionTable
          transactions={transactions}
          loaded={loaded}
          selectedRef={selectedRef}
          onSelect={(ref) => setSelectedRef((current) => (current === ref ? null : ref))}
        />
        <Inspector
          transaction={selected}
          agedOut={agedOut}
          onOpenGraph={() => setGraphOpen(true)}
        />
      </div>

      {graphOpen && selected && (
        <MuleGraphModal
          transaction={selected}
          flag={selected.agent_flags?.find(isMuleFlag)}
          onClose={() => setGraphOpen(false)}
        />
      )}
    </div>
  );
}

function Kpi({ label, value, tone = 'text-slate-50', icon: Icon, hint }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 px-4 py-3" title={hint}>
      <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-slate-500">
        {Icon && <Icon className="h-3.5 w-3.5" aria-hidden="true" />}
        {label}
      </p>
      <p className={`mt-1 font-mono text-2xl font-semibold ${tone}`}>{value}</p>
    </div>
  );
}

function TransactionTable({ transactions, loaded, selectedRef, onSelect }) {
  if (!loaded) {
    return (
      <div className="flex h-64 items-center justify-center rounded-xl border border-slate-800 bg-slate-900 text-sm text-slate-500">
        <RefreshCw className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" /> Connecting to live feed…
      </div>
    );
  }
  if (transactions.length === 0) {
    return (
      <div className="flex h-64 flex-col items-center justify-center rounded-xl border border-slate-800 bg-slate-900 text-center">
        <Inbox className="h-6 w-6 text-slate-600" aria-hidden="true" />
        <p className="mt-2 text-sm text-slate-400">No transactions yet.</p>
        <p className="text-xs text-slate-500">Sign in as <span className="font-mono">sandbox</span> in another tab and send money.</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900">
      <div className="scroll-thin max-h-[640px] overflow-auto">
        <table className="w-full min-w-[720px] text-left text-xs">
          <thead className="sticky top-0 z-10 bg-slate-900/95 text-[11px] uppercase tracking-wider text-slate-500 backdrop-blur">
            <tr className="border-b border-slate-800">
              <th scope="col" className="px-3 py-2.5 font-medium">Ref</th>
              <th scope="col" className="px-3 py-2.5 font-medium">Time</th>
              <th scope="col" className="px-3 py-2.5 font-medium">Account</th>
              <th scope="col" className="px-3 py-2.5 text-right font-medium">Amount</th>
              <th scope="col" className="px-3 py-2.5 text-right font-medium">Score</th>
              <th scope="col" className="px-3 py-2.5 font-medium">Decision</th>
              <th scope="col" className="px-3 py-2.5 font-medium">Agent flags</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/70">
            {transactions.map((t) => {
              const active = t.transaction_ref === selectedRef;
              return (
                <tr
                  key={t.transaction_ref}
                  onClick={() => onSelect(t.transaction_ref)}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect(t.transaction_ref))}
                  tabIndex={0}
                  aria-selected={active}
                  className={`cursor-pointer transition-colors ${active ? 'bg-emerald-500/10' : 'hover:bg-slate-800/50'}`}
                >
                  <td className={`px-3 py-2 font-mono ${active ? 'text-emerald-300' : 'text-slate-400'}`}>#{t.transaction_ref}</td>
                  <td className="px-3 py-2 font-mono text-slate-500">{formatTime(t.timestamp)}</td>
                  <td className="px-3 py-2 font-mono text-slate-200">{t.payload?.account_id ?? '—'}</td>
                  <td className="px-3 py-2 text-right font-mono text-slate-200">{formatNPR(t.payload?.amount)}</td>
                  <td className="px-3 py-2 text-right font-mono text-slate-300">{formatPct(t.fraud_score)}</td>
                  <td className="px-3 py-2"><DecisionBadge decision={t.decision} /></td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {(t.agent_flags ?? []).length === 0 ? (
                        <span className="text-slate-600">—</span>
                      ) : (
                        t.agent_flags.map((f) => (
                          <span
                            key={f.agent}
                            title={`${f.reason}${f.detail ? ` — ${f.detail}` : ''}`}
                            className={`rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${
                              f.decision === 'BLOCK' ? 'bg-red-500/10 text-red-300 ring-red-500/30' : 'bg-amber-500/10 text-amber-300 ring-amber-500/30'
                            }`}
                          >
                            {AGENT_LABELS[f.agent] ?? f.agent}
                          </span>
                        ))
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Inspector({ transaction, agedOut, onOpenGraph }) {
  if (!transaction) {
    return (
      <aside className="flex min-h-64 flex-col items-center justify-center rounded-xl border border-dashed border-slate-800 p-6 text-center xl:sticky xl:top-20">
        <ScanSearch className="h-6 w-6 text-slate-600" aria-hidden="true" />
        <p className="mt-2 text-sm text-slate-400">
          {agedOut ? 'That transaction has rolled out of the live window (last 50).' : 'Select a transaction to inspect it.'}
        </p>
      </aside>
    );
  }

  const flags = transaction.agent_flags ?? [];
  const muleFlag = flags.find(isMuleFlag);
  const p = transaction.payload ?? {};

  return (
    <aside className="space-y-4 xl:sticky xl:top-20" aria-label={`Inspector for transaction ${transaction.transaction_ref}`}>
      <section className="rounded-xl border border-slate-800 bg-slate-900 p-4">
        <div className="flex items-start justify-between">
          <div>
            <p className="font-mono text-xs text-slate-500">#{transaction.transaction_ref} · {formatTime(transaction.timestamp)} · by {transaction.submitted_by}</p>
            <p className="mt-1 font-mono text-sm text-slate-200">
              {p.account_id} → {p.target_account || '—'}
            </p>
          </div>
          <DecisionBadge decision={transaction.decision} size="lg" />
        </div>

        <div className="mt-4 flex items-end justify-between rounded-lg bg-slate-950/60 px-4 py-3">
          <div>
            <p className="text-[11px] uppercase tracking-wider text-slate-500">Fraud probability</p>
            <p className="font-mono text-3xl font-semibold text-slate-50">{((transaction.fraud_score ?? 0) * 100).toFixed(2)}%</p>
          </div>
          <div className="text-right">
            <p className="text-[11px] uppercase tracking-wider text-slate-500">ML baseline</p>
            <DecisionBadge decision={transaction.ml_decision} />
          </div>
        </div>

        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          <Detail label="Amount" value={formatNPR(p.amount)} />
          <Detail label="Type" value={p.transaction_type} />
          <Detail label="Hour" value={p.hour_of_day != null ? `${String(p.hour_of_day).padStart(2, '0')}:00` : '—'} />
          <Detail label="District" value={p.district || '—'} />
          <Detail label="Device" value={p.device_id} />
          <Detail label="VPN" value={p.is_vpn ? 'Yes' : 'No'} />
        </dl>

        {flags.length > 0 && (
          <ul className="mt-4 space-y-1.5 border-t border-slate-800 pt-3">
            {flags.map((f) => (
              <li key={f.agent} className="text-xs">
                <span className="font-semibold text-slate-200">{AGENT_LABELS[f.agent] ?? f.agent}</span>
                <span className="text-slate-500"> → {f.decision}</span>
                <span className="text-slate-400"> · {f.reason}</span>
                {f.detail && <p className="font-mono text-[11px] text-slate-500">{f.detail}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <ShapBreakdown shapValues={transaction.shap_values} fraudScore={transaction.fraud_score} />

      <section className={`rounded-xl border p-4 ${muleFlag ? 'border-red-500/40 bg-red-500/[0.06]' : 'border-slate-800 bg-slate-900'}`}>
        <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
          <Network className={`h-4 w-4 ${muleFlag ? 'text-red-400' : 'text-slate-500'}`} aria-hidden="true" /> Fraud ring analysis
        </h3>
        {muleFlag ? (
          <>
            <p className="mt-1 text-xs text-slate-300">{muleFlag.reason}</p>
            <p className="font-mono text-[11px] text-slate-500">{muleFlag.detail}</p>
            <button
              type="button"
              onClick={onOpenGraph}
              className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-red-500 py-2 text-xs font-semibold text-white hover:bg-red-400"
            >
              <Network className="h-3.5 w-3.5" aria-hidden="true" /> Open mule network graph
            </button>
          </>
        ) : (
          <p className="mt-1 text-xs text-slate-500">The Graph Agent did not flag this transaction.</p>
        )}
      </section>
    </aside>
  );
}

function Detail({ label, value }) {
  return (
    <>
      <dt className="text-slate-500">{label}</dt>
      <dd className="truncate text-right font-mono text-slate-200">{value ?? '—'}</dd>
    </>
  );
}
