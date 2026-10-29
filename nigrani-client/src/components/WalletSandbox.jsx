import { useState } from 'react';
import {
  ArrowRightLeft,
  Banknote,
  ChevronDown,
  Clock,
  Fingerprint,
  Globe,
  LoaderCircle,
  MapPin,
  Send,
  Smartphone,
} from 'lucide-react';
import client, { describeError } from '../api/client.js';
import { AGENT_LABELS, formatNPR, formatPct } from '../lib/format.js';
import DecisionBadge from './DecisionBadge.jsx';
import ErrorBanner from './ErrorBanner.jsx';

const TRANSFER_TYPES = [
  { value: 'ESEWA_P2P', label: 'P2P Transfer', icon: ArrowRightLeft },
  { value: 'ATM_WITHDRAWAL', label: 'ATM Withdrawal', icon: Banknote },
];

const INITIAL_FORM = {
  account_id: 'ACC-8622639',
  target_account: 'ACC-2522726',
  amount: '2500',
  transaction_type: 'ESEWA_P2P',
  hour_of_day: new Date().getHours(),
  district: 'Kathmandu',
  device_id: 'DEV-0AE666',
  is_vpn: false,
  // Advanced telemetry (required or used by the API, hidden by default)
  ip_address: '112.196.26.109',
  impossible_travel: false,
  km_from_home_district: '0',
};

const RECEIPT_STYLES = {
  ALLOW: { ring: 'border-emerald-500/40 bg-emerald-500/[0.07]', title: 'text-emerald-300', headline: 'Payment sent' },
  OTP: { ring: 'border-yellow-500/40 bg-yellow-500/[0.07]', title: 'text-yellow-300', headline: 'Verify it’s you — OTP sent' },
  DUAL_OTP: { ring: 'border-amber-500/50 bg-amber-500/[0.08]', title: 'text-amber-300', headline: 'Extra verification — phone & email OTP' },
  BLOCK: { ring: 'border-red-500/50 bg-red-500/[0.08]', title: 'text-red-300', headline: 'Payment blocked for your protection' },
};

const inputClass =
  'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-emerald-500/60 focus:outline-none';

function Field({ label, icon: Icon, children, hint }) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1.5 text-xs font-medium text-slate-400">
        {Icon && <Icon className="h-3.5 w-3.5" aria-hidden="true" />}
        {label}
      </span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-slate-500">{hint}</span>}
    </label>
  );
}

export default function WalletSandbox() {
  const [form, setForm] = useState(INITIAL_FORM);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [receipt, setReceipt] = useState(null);

  const update = (key) => (event) => {
    const value = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
    setForm((f) => ({ ...f, [key]: value }));
  };

  async function handleSubmit(event) {
    event.preventDefault();
    const amount = Number(form.amount);
    if (!form.account_id.trim()) return setError('Enter the sending account.');
    if (!Number.isFinite(amount) || amount <= 0) return setError('Enter an amount greater than zero.');
    if (!form.device_id.trim()) return setError('Enter a device ID.');

    const payload = {
      account_id: form.account_id.trim(),
      target_account: form.target_account.trim() || null,
      amount,
      transaction_type: form.transaction_type,
      hour_of_day: Number(form.hour_of_day),
      district: form.district.trim() || null,
      device_id: form.device_id.trim(),
      is_vpn: form.is_vpn,
      ip_address: form.ip_address.trim() || '0.0.0.0',
      impossible_travel: form.impossible_travel,
      km_from_home_district: Math.max(0, Number(form.km_from_home_district) || 0),
    };

    setSubmitting(true);
    setError('');
    try {
      const { data } = await client.post('/score-transaction', payload);
      setReceipt({ payload, result: data });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setSubmitting(false);
    }
  }

  const hour = Number(form.hour_of_day);
  const isNight = hour >= 0 && hour <= 5;

  return (
    <div className="mx-auto grid max-w-5xl items-start gap-6 lg:grid-cols-[400px_1fr]">
      {/* Phone frame */}
      <div className="mx-auto w-full max-w-[400px] overflow-hidden rounded-[2rem] border border-slate-800 bg-slate-900 shadow-2xl shadow-black/40">
        <div className="bg-gradient-to-br from-violet-600 via-indigo-600 to-sky-600 px-6 pb-6 pt-5">
          <div className="flex items-center justify-between text-[11px] font-medium uppercase tracking-[0.14em] text-white/80">
            <span>Nigrani Pay</span>
            <span className="flex items-center gap-1"><Smartphone className="h-3.5 w-3.5" aria-hidden="true" /> Sandbox</span>
          </div>
          <p className="mt-4 text-xs text-white/70">Available balance</p>
          <p className="font-mono text-3xl font-semibold tracking-tight text-white">NPR 2,48,750.00</p>
          <p className="mt-1 font-mono text-xs text-white/70">{form.account_id || '—'}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 p-5" noValidate>
          <div className="grid grid-cols-2 gap-2 rounded-xl bg-slate-950 p-1" role="radiogroup" aria-label="Transfer type">
            {TRANSFER_TYPES.map(({ value, label, icon: Icon }) => {
              const active = form.transaction_type === value;
              return (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => setForm((f) => ({ ...f, transaction_type: value }))}
                  className={`flex items-center justify-center gap-1.5 rounded-lg py-2 text-xs font-semibold transition ${
                    active ? 'bg-slate-800 text-slate-50 shadow' : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" /> {label}
                </button>
              );
            })}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label="From account">
              <input value={form.account_id} onChange={update('account_id')} className={`${inputClass} font-mono`} />
            </Field>
            <Field label={form.transaction_type === 'ATM_WITHDRAWAL' ? 'ATM / terminal' : 'To account'}>
              <input value={form.target_account} onChange={update('target_account')} className={`${inputClass} font-mono`} />
            </Field>
          </div>

          <Field label="Amount (NPR)">
            <input
              type="number"
              inputMode="decimal"
              min="1"
              step="any"
              value={form.amount}
              onChange={update('amount')}
              className={`${inputClass} font-mono text-lg`}
            />
          </Field>

          <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
            <p className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Session context</p>
            <div className="space-y-3">
              <Field label="Time of day" icon={Clock}>
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min="0"
                    max="23"
                    value={form.hour_of_day}
                    onChange={update('hour_of_day')}
                    className="flex-1 accent-emerald-500"
                    aria-label="Hour of day"
                  />
                  <span className={`w-14 text-right font-mono text-sm ${isNight ? 'text-amber-300' : 'text-slate-200'}`}>
                    {String(hour).padStart(2, '0')}:00
                  </span>
                </div>
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="District" icon={MapPin}>
                  <input value={form.district} onChange={update('district')} className={inputClass} />
                </Field>
                <Field label="Device ID" icon={Fingerprint}>
                  <input value={form.device_id} onChange={update('device_id')} className={`${inputClass} font-mono`} />
                </Field>
              </div>
              <label className="flex cursor-pointer items-center justify-between rounded-lg border border-slate-800 px-3 py-2">
                <span className="flex items-center gap-1.5 text-xs font-medium text-slate-300">
                  <Globe className="h-3.5 w-3.5" aria-hidden="true" /> Connected via VPN
                </span>
                <input type="checkbox" checked={form.is_vpn} onChange={update('is_vpn')} className="h-4 w-4 accent-emerald-500" />
              </label>
            </div>
          </div>

          <div>
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              aria-expanded={showAdvanced}
              className="flex w-full items-center justify-between text-xs font-medium text-slate-500 hover:text-slate-300"
            >
              Advanced telemetry
              <ChevronDown className={`h-4 w-4 transition ${showAdvanced ? 'rotate-180' : ''}`} aria-hidden="true" />
            </button>
            {showAdvanced && (
              <div className="mt-3 space-y-3">
                <Field label="IP address">
                  <input value={form.ip_address} onChange={update('ip_address')} className={`${inputClass} font-mono`} />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Distance from home (km)">
                    <input
                      type="number"
                      min="0"
                      value={form.km_from_home_district}
                      onChange={update('km_from_home_district')}
                      className={`${inputClass} font-mono`}
                    />
                  </Field>
                  <label className="flex cursor-pointer items-center gap-2 self-end rounded-lg border border-slate-800 px-3 py-2 text-xs text-slate-300">
                    <input
                      type="checkbox"
                      checked={form.impossible_travel}
                      onChange={update('impossible_travel')}
                      className="h-4 w-4 accent-emerald-500"
                    />
                    Impossible travel
                  </label>
                </div>
              </div>
            )}
          </div>

          <ErrorBanner message={error} offline={error.startsWith('Cannot reach')} onDismiss={() => setError('')} />

          <button
            type="submit"
            disabled={submitting}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-500 py-3 text-sm font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Send className="h-4 w-4" aria-hidden="true" />}
            {submitting ? 'Securing payment…' : 'Send Money'}
          </button>
        </form>
      </div>

      <div className="space-y-4">
        {receipt ? <VerdictReceipt {...receipt} /> : <ReceiptPlaceholder />}
        <DemoScenarios />
      </div>
    </div>
  );
}

function VerdictReceipt({ payload, result }) {
  const style = RECEIPT_STYLES[result.decision] ?? RECEIPT_STYLES.OTP;
  const overridden = result.decision !== result.ml_decision;
  const flags = result.agent_flags ?? [];

  return (
    <section aria-live="polite" className={`rounded-2xl border p-5 ${style.ring}`}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Transaction verdict</p>
          <h2 className={`mt-1 text-lg font-semibold ${style.title}`}>{style.headline}</h2>
        </div>
        <DecisionBadge decision={result.decision} size="lg" />
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Risk score" value={formatPct(result.fraud_score)} />
        <Stat label="ML baseline" value={<DecisionBadge decision={result.ml_decision} />} />
        <Stat label="Amount" value={formatNPR(payload.amount)} />
        <Stat label="Reference" value={`#${result.transaction_ref ?? '—'}`} />
      </dl>

      {overridden && (
        <p className="mt-4 text-xs text-slate-400">
          Agents overrode the ML model: <span className="font-mono">{result.ml_decision}</span> →{' '}
          <span className="font-mono text-slate-200">{result.decision}</span>
        </p>
      )}

      <div className="mt-4">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Agent flags</p>
        {flags.length === 0 ? (
          <p className="mt-1.5 text-sm text-slate-400">No deterministic agents fired — the ML model’s decision stands.</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {flags.map((flag) => (
              <li key={flag.agent} className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
                <DecisionBadge decision={flag.decision} />
                <div className="min-w-0">
                  <p className="text-sm text-slate-200">
                    <span className="font-semibold">{AGENT_LABELS[flag.agent] ?? flag.agent}</span> · {flag.reason}
                  </p>
                  {flag.detail && <p className="truncate font-mono text-[11px] text-slate-500">{flag.detail}</p>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function Stat({ label, value }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2">
      <dt className="text-[11px] text-slate-500">{label}</dt>
      <dd className="mt-0.5 font-mono text-sm text-slate-100">{value}</dd>
    </div>
  );
}

function ReceiptPlaceholder() {
  return (
    <div className="rounded-2xl border border-dashed border-slate-800 p-8 text-center">
      <Send className="mx-auto h-6 w-6 text-slate-600" aria-hidden="true" />
      <p className="mt-2 text-sm text-slate-400">Send a payment to see Nigrani’s real-time verdict.</p>
    </div>
  );
}

function DemoScenarios() {
  const scenarios = [
    ['ML risk', 'VPN + impossible travel + 300 km (Advanced)'],
    ['SIM Swap', 'Any device other than DEV-0AE666'],
    ['Mule Network', 'From ACC-0011204, amount over NPR 50,000'],
    ['Behavior', 'Two of: hour 0–5, new district, amount > NPR 20,000'],
    ['Velocity', '4 payments from one account within a minute'],
  ];
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Presenter scenarios</p>
      <ul className="mt-3 space-y-2 text-sm">
        {scenarios.map(([name, how]) => (
          <li key={name} className="flex gap-3">
            <span className="w-28 shrink-0 font-medium text-slate-200">{name}</span>
            <span className="text-slate-400">{how}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
