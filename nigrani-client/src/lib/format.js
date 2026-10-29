export const FEATURE_LABELS = {
  amount: 'Amount',
  txn_count_1h: 'Txns in last 1h',
  txn_count_24h: 'Txns in last 24h',
  total_amount_24h_npr: 'Amount in last 24h',
  weekend_flag: 'Weekend',
  churn_risk_score: 'Churn risk score',
  impossible_travel: 'Impossible travel',
  is_vpn: 'VPN',
  km_from_home_district: 'Distance from home',
  degree_in: 'Inbound connections',
  degree_out: 'Outbound connections',
  kyc_tier: 'KYC tier',
  risk_tier: 'Risk tier',
  age_group: 'Age group',
};

export const AGENT_LABELS = {
  velocity_agent: 'Velocity',
  sim_swap_agent: 'SIM Swap',
  graph_agent: 'Graph',
  behavior_agent: 'Behavior',
};

const npr = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const formatNPR = (value) => `NPR ${npr.format(Number(value) || 0)}`;

export const formatPct = (value, digits = 1) => `${((Number(value) || 0) * 100).toFixed(digits)}%`;

/** SHAP values are probability deltas; show them as signed percentage points. */
export const formatPP = (value) => {
  const pp = (Number(value) || 0) * 100;
  return `${pp >= 0 ? '+' : '−'}${Math.abs(pp).toFixed(2)} pp`;
};

export const formatTime = (iso) => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
};

export const isMuleFlag = (flag) => flag?.agent === 'graph_agent';
