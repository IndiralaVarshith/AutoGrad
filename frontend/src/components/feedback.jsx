import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, Info, X, XCircle } from 'lucide-react';
import { Button, cx } from './ui';

const FeedbackContext = createContext(null);
const ICONS = {
  success: { icon: CheckCircle2, color: 'text-good' },
  error: { icon: XCircle, color: 'text-bad' },
  info: { icon: Info, color: 'text-info' },
};

function ConfirmDialog({ request, onClose }) {
  const ref = useRef(null);

  useEffect(() => {
    const dialog = ref.current;
    if (request && !dialog.open) dialog.showModal();
    if (!request && dialog.open) dialog.close();
  }, [request]);

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby="confirm-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose(false);
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose(false);
      }}
    >
      {request && (
        <div className="p-6">
          <h2 id="confirm-title" className="text-xl">{request.title}</h2>
          {request.body && <p className="mt-2 whitespace-pre-line text-sm text-muted">{request.body}</p>}
          <div className="mt-6 flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={() => onClose(false)}>{request.cancelLabel || 'Cancel'}</Button>
            <Button
              variant="primary"
              autoFocus
              className={request.danger ? 'bg-bad! hover:opacity-90' : undefined}
              onClick={() => onClose(true)}
            >
              {request.confirmLabel || 'Continue'}
            </Button>
          </div>
        </div>
      )}
    </dialog>
  );
}

export function FeedbackProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [request, setRequest] = useState(null);
  const resolver = useRef(null);
  const nextId = useRef(1);

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback((type, message) => {
    const id = nextId.current;
    nextId.current += 1;
    setToasts((list) => [...list.slice(-3), { id, type, message }]);
    // An error stays until it is read and closed.
    if (type !== 'error') setTimeout(() => dismiss(id), 4500);
  }, [dismiss]);

  const value = useMemo(() => ({
    toast: {
      success: (message) => push('success', message),
      error: (message) => push('error', message),
      info: (message) => push('info', message),
    },
    confirm: (options) => new Promise((resolve) => {
      resolver.current = resolve;
      setRequest(options);
    }),
  }), [push]);

  const closeConfirm = (answer) => {
    resolver.current?.(answer);
    resolver.current = null;
    setRequest(null);
  };

  return (
    <FeedbackContext.Provider value={value}>
      {children}
      <ConfirmDialog request={request} onClose={closeConfirm} />
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-4 bottom-20 z-[70] flex flex-col items-center gap-2 sm:inset-x-auto sm:right-5 sm:bottom-5 sm:items-end"
      >
        {toasts.map((toast) => {
          const { icon: Icon, color } = ICONS[toast.type];
          return (
            <div
              key={toast.id}
              role={toast.type === 'error' ? 'alert' : 'status'}
              className="toast-in pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-xl border border-line bg-surface py-3 pr-2 pl-3.5 shadow-float"
            >
              <Icon size={18} className={cx('mt-0.5 shrink-0', color)} aria-hidden="true" />
              <div className="min-w-0 flex-1 pt-px text-sm break-words">{toast.message}</div>
              <button
                type="button"
                onClick={() => dismiss(toast.id)}
                aria-label="Dismiss"
                className="icon-btn -my-1 size-7 shrink-0"
              >
                <X size={15} aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </FeedbackContext.Provider>
  );
}

export const useFeedback = () => useContext(FeedbackContext);
