import { TriangleAlert, WifiOff, X } from 'lucide-react';

export default function ErrorBanner({ message, offline = false, onDismiss }) {
  if (!message) return null;
  const Icon = offline ? WifiOff : TriangleAlert;
  return (
    <div role="alert" className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-red-400" aria-hidden="true" />
      <p className="flex-1 leading-relaxed">{message}</p>
      {onDismiss && (
        <button type="button" onClick={onDismiss} className="rounded p-0.5 text-red-300 hover:bg-red-500/20" aria-label="Dismiss">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
