import { Component } from 'react';
import { RefreshCw, TriangleAlert } from 'lucide-react';

/** Last line of defense: a render bug shows a recovery screen, never a blank page. */
export default class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('Nigrani UI error:', error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 p-6">
        <div className="max-w-md rounded-xl border border-slate-800 bg-slate-900 p-6 text-center">
          <TriangleAlert className="mx-auto h-8 w-8 text-amber-400" aria-hidden="true" />
          <h1 className="mt-3 text-lg font-semibold text-slate-100">Something went wrong</h1>
          <p className="mt-1 text-sm text-slate-400">The interface hit an unexpected error. Your session is intact.</p>
          <button
            type="button"
            onClick={() => this.setState({ error: null })}
            className="mt-5 inline-flex items-center gap-2 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" /> Recover
          </button>
        </div>
      </div>
    );
  }
}
