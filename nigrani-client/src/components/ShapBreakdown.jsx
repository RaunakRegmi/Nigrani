import { Bar, BarChart, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { FEATURE_LABELS, formatPP } from '../lib/format.js';

const RISK_UP = '#f87171'; // red-400: pushes toward fraud
const RISK_DOWN = '#34d399'; // emerald-400: pushes toward legit

// Single-line tick: Recharts' default wraps long feature names into the bars
function FeatureTick({ x, y, payload }) {
  return (
    <text x={x} y={y} dy={4} textAnchor="end" fill="#cbd5e1" fontSize={12}>
      {payload.value}
    </text>
  );
}

function ShapTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const { label, value } = payload[0].payload;
  return (
    <div className="rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs shadow-lg">
      <p className="text-slate-300">{label}</p>
      <p className="font-mono text-slate-100">{formatPP(value)}</p>
      <p className="text-slate-500">{value > 0 ? 'increases fraud risk' : 'reduces fraud risk'}</p>
    </div>
  );
}

export default function ShapBreakdown({ shapValues, fraudScore }) {
  const rows = Object.entries(shapValues ?? {})
    .map(([feature, value]) => ({ feature, label: FEATURE_LABELS[feature] ?? feature, value: Number(value) || 0 }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-slate-100">SHAP explainability</h3>
        <span className="text-[11px] text-slate-500">top drivers of the ML score</span>
      </div>

      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">No explanation was returned for this transaction.</p>
      ) : (
        <>
          <div className="mt-3 h-[150px]" role="img" aria-label="Feature contributions to the fraud score">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 12, bottom: 4, left: 4 }} barCategoryGap="28%">
                <XAxis type="number" hide domain={[(min) => Math.min(min, 0) * 1.1, (max) => Math.max(max, 0) * 1.1]} />
                <YAxis type="category" dataKey="label" width={140} tickLine={false} axisLine={false} tick={<FeatureTick />} />
                <ReferenceLine x={0} stroke="#475569" />
                <Tooltip content={<ShapTooltip />} cursor={{ fill: 'rgba(148,163,184,0.06)' }} />
                <Bar dataKey="value" radius={4} isAnimationActive={false}>
                  {rows.map((row) => (
                    <Cell key={row.feature} fill={row.value > 0 ? RISK_UP : RISK_DOWN} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <ul className="mt-2 divide-y divide-slate-800 border-t border-slate-800">
            {rows.map((row) => (
              <li key={row.feature} className="flex items-center justify-between py-1.5 text-xs">
                <span className="flex items-center gap-2 text-slate-300">
                  <span className="h-2 w-2 rounded-sm" style={{ background: row.value > 0 ? RISK_UP : RISK_DOWN }} aria-hidden="true" />
                  {row.label}
                  <span className="text-slate-500">{row.value > 0 ? '↑ risk' : '↓ risk'}</span>
                </span>
                <span className="font-mono text-slate-100">{formatPP(row.value)}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-slate-500">
            Contributions in percentage points relative to the model’s baseline; final ML score{' '}
            <span className="font-mono text-slate-400">{((fraudScore ?? 0) * 100).toFixed(2)}%</span>.
          </p>
        </>
      )}
    </section>
  );
}
