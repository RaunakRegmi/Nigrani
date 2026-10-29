# nigrani-client

React + Vite + Tailwind CSS v4 frontend for the Nigrani fraud detection API.

## Run

```bash
# 1. API (from the project root) — allows CORS from http://127.0.0.1:5173 by default
venv/bin/uvicorn api:app --host 127.0.0.1 --port 8000

# 2. Frontend
cd nigrani-client
npm install
npm run dev        # http://127.0.0.1:5173
```

Sign in as `sandbox` (Digital Wallet) or `analyst` (Command Center), password `demo`.

## Configuration

| Variable | Where | Default |
|---|---|---|
| `VITE_API_BASE_URL` | `nigrani-client/.env.local` | `http://127.0.0.1:8000/api/v1` |
| `NIGRANI_CORS_ORIGINS` | API environment | `http://127.0.0.1:5173,http://localhost:5173` |

If you serve the frontend from another origin (e.g. `npm run preview` on port 4173),
add it to `NIGRANI_CORS_ORIGINS` or the browser will block API calls.

## Structure

```
src/
  api/client.js              Axios instance, JWT interceptor, 401 → login, error messages
  App.jsx                    Session restore, nav bar, role routing (views lazy-loaded)
  components/
    Login.jsx                Glassmorphism sign-in
    WalletSandbox.jsx        Mobile wallet simulator + verdict receipt
    AnalystMonitor.jsx       2s polling feed, KPIs, table, inspector
    ShapBreakdown.jsx        SHAP feature contributions (Recharts)
    MuleGraphModal.jsx       Mule network visualization (mock senders)
    DecisionBadge.jsx        ALLOW / OTP / DUAL OTP / BLOCK badges (icon + label)
    ErrorBanner.jsx          Network / API error banner
    ErrorBoundary.jsx        Render-crash recovery screen
  lib/format.js              Labels and number formatting
```
