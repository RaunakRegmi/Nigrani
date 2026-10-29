import { useEffect, useMemo, useRef } from 'react';
import { Network, X } from 'lucide-react';
import { formatNPR } from '../lib/format.js';

// Deterministic PRNG so the mock ring is identical every time a transaction is opened
function seededRandom(seed) {
  let h = 2166136261;
  for (const ch of seed) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return ((h ^= h >>> 16) >>> 0) / 4294967296;
  };
}

function buildRing(collector, ref) {
  const rand = seededRandom(`${collector}-${ref}`);
  const count = 15 + Math.floor(rand() * 6); // 15-20 senders
  const amounts = [4900, 9800, 14500, 19900, 24500, 49000];
  return Array.from({ length: count }, (_, i) => {
    const angle = (2 * Math.PI * i) / count + (rand() - 0.5) * 0.15;
    const radius = 150 + rand() * 30;
    return {
      id: `ACC-${String(1000000 + Math.floor(rand() * 8999999))}`,
      x: 220 + radius * Math.cos(angle),
      y: 220 + radius * Math.sin(angle),
      amount: amounts[Math.floor(rand() * amounts.length)],
      minutesAgo: 2 + Math.floor(rand() * 178),
    };
  });
}

export default function MuleGraphModal({ transaction, flag, onClose }) {
  const closeRef = useRef(null);
  const collector = transaction.payload?.account_id ?? 'UNKNOWN';
  const senders = useMemo(() => buildRing(collector, transaction.transaction_ref), [collector, transaction.transaction_ref]);
  const total = senders.reduce((sum, s) => sum + s.amount, 0);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="mule-title"
        className="w-full max-w-2xl rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-slate-800 px-5 py-4">
          <div>
            <h2 id="mule-title" className="flex items-center gap-2 text-base font-semibold text-slate-100">
              <Network className="h-4 w-4 text-red-400" aria-hidden="true" /> Mule Network Signature
            </h2>
            <p className="mt-0.5 font-mono text-xs text-slate-400">{flag?.detail}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-5">
          <svg viewBox="0 0 440 440" className="mx-auto w-full max-w-md" role="img" aria-label={`${senders.length} sender accounts flowing into ${collector}`}>
            <defs>
              {/* userSpaceOnUse: arrowheads stay one size regardless of edge thickness */}
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill="#ef4444" />
              </marker>
            </defs>
            {senders.map((s) => {
              // Stop the edge at the collector's edge so the arrowhead stays visible
              const dx = 220 - s.x;
              const dy = 220 - s.y;
              const len = Math.hypot(dx, dy);
              const stop = (len - 34) / len;
              return (
                <line
                  key={`edge-${s.id}`}
                  x1={s.x}
                  y1={s.y}
                  x2={s.x + dx * stop}
                  y2={s.y + dy * stop}
                  stroke="#ef4444"
                  strokeOpacity="0.7"
                  strokeWidth={1 + s.amount / 25000}
                  markerEnd="url(#arrow)"
                />
              );
            })}
            {senders.map((s) => (
              <g key={s.id}>
                <circle cx={s.x} cy={s.y} r="8" fill="#64748b" stroke="#0f172a" strokeWidth="2">
                  <title>{`${s.id} · sent ${formatNPR(s.amount)} · ${s.minutesAgo} min ago`}</title>
                </circle>
              </g>
            ))}
            <circle cx="220" cy="220" r="26" fill="#dc2626" stroke="#0f172a" strokeWidth="3">
              <title>{`Collector ${collector}`}</title>
            </circle>
            {/* Opaque pill keeps the label legible over the edges */}
            <rect x="166" y="252" width="108" height="22" rx="6" fill="#0f172a" stroke="#dc2626" strokeOpacity="0.6" />
            <text x="220" y="267" textAnchor="middle" className="fill-slate-100 font-mono text-[11px]">{collector}</text>
          </svg>

          <div className="mt-4 grid grid-cols-3 gap-3 text-center">
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 py-2">
              <p className="font-mono text-lg text-slate-100">{senders.length}</p>
              <p className="text-[11px] text-slate-500">inbound senders</p>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 py-2">
              <p className="font-mono text-lg text-slate-100">{formatNPR(total)}</p>
              <p className="text-[11px] text-slate-500">inbound in 3h</p>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 py-2">
              <p className="font-mono text-lg text-red-300">1</p>
              <p className="text-[11px] text-slate-500">collector</p>
            </div>
          </div>
          <p className="mt-3 text-center text-[11px] text-slate-500">
            Simulated sender network for illustration — hover a node for details. Real graph data is not yet wired in.
          </p>
        </div>
      </div>
    </div>
  );
}
