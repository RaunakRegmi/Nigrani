import { Ban, CircleCheck, KeyRound, ShieldAlert } from 'lucide-react';

// Status is always icon + label, never color alone
export const DECISION_META = {
  ALLOW: { label: 'ALLOW', icon: CircleCheck, badge: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' },
  OTP: { label: 'OTP', icon: KeyRound, badge: 'bg-yellow-500/10 text-yellow-300 ring-yellow-500/30' },
  DUAL_OTP: { label: 'DUAL OTP', icon: ShieldAlert, badge: 'bg-amber-500/10 text-amber-300 ring-amber-500/40' },
  BLOCK: { label: 'BLOCK', icon: Ban, badge: 'bg-red-500/10 text-red-300 ring-red-500/40' },
};

const FALLBACK = { label: 'UNKNOWN', icon: ShieldAlert, badge: 'bg-slate-500/10 text-slate-300 ring-slate-500/30' };

export default function DecisionBadge({ decision, size = 'sm' }) {
  const meta = DECISION_META[decision] ?? { ...FALLBACK, label: decision ?? FALLBACK.label };
  const Icon = meta.icon;
  const sizing = size === 'lg' ? 'px-3 py-1 text-sm gap-1.5' : 'px-2 py-0.5 text-[11px] gap-1';
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded-md font-semibold tracking-wide ring-1 ring-inset ${sizing} ${meta.badge}`}>
      <Icon className={size === 'lg' ? 'h-4 w-4' : 'h-3 w-3'} aria-hidden="true" />
      {meta.label}
    </span>
  );
}
