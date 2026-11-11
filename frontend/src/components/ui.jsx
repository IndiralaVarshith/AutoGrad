import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronDown, FileText, Info, Loader2, UploadCloud, X, XCircle } from 'lucide-react';

export const cx = (...parts) => parts.filter(Boolean).join(' ');

/** The server's own explanation when it gave one, otherwise the fallback. */
export function errorText(err, fallback = 'Something went wrong. Please try again.') {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail?.message) return detail.message;
  if (err?.message === 'Network Error') return 'Could not reach the server. Is the backend running?';
  return err?.message || fallback;
}

export const formatBytes = (bytes) => (
  bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
);

export function Button({ variant = 'secondary', size, loading = false, icon: Icon, children, className, disabled, ...props }) {
  return (
    <button
      type="button"
      {...props}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx('btn', `btn-${variant}`, size && `btn-${size}`, className)}
    >
      {loading ? <Loader2 size={16} className="spin" aria-hidden="true" /> : Icon && <Icon size={16} aria-hidden="true" />}
      {children}
    </button>
  );
}

export function PageHeader({ title, subtitle, actions, eyebrow }) {
  return (
    <div className="flex flex-col gap-4 pb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-[13px] font-semibold text-primary">{eyebrow}</div>}
        <h1 className="text-[1.75rem] sm:text-[2rem]">{title}</h1>
        {subtitle && <p className="mt-1.5 max-w-2xl text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

const CALLOUTS = {
  info: { icon: Info, box: 'bg-info-soft', text: 'text-info' },
  good: { icon: CheckCircle2, box: 'bg-good-soft', text: 'text-good' },
  warn: { icon: AlertTriangle, box: 'bg-warn-soft', text: 'text-warn' },
  bad: { icon: XCircle, box: 'bg-bad-soft', text: 'text-bad' },
};

export function Callout({ tone = 'info', title, children, action, onDismiss, className }) {
  const style = CALLOUTS[tone];
  const Icon = style.icon;
  return (
    <div className={cx('rise flex gap-3 rounded-xl px-4 py-3', style.box, className)} role={tone === 'bad' ? 'alert' : undefined}>
      <Icon size={18} className={cx('mt-0.5 shrink-0', style.text)} aria-hidden="true" />
      <div className="min-w-0 flex-1 text-sm">
        {title && <div className={cx('font-semibold', style.text)}>{title}</div>}
        {children && <div className="text-ink">{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
      {onDismiss && (
        <button type="button" onClick={onDismiss} aria-label="Dismiss" className="icon-btn -my-1 -mr-2 shrink-0">
          <X size={16} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

/** A callout that stays one line until the teacher opens it. */
export function FoldingCallout({ tone = 'warn', title, items, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const style = CALLOUTS[tone];
  const Icon = style.icon;
  const panel = useId();
  return (
    <div className={cx('rise rounded-xl', style.box)}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={panel}
        className="flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left text-sm"
      >
        <Icon size={18} className={cx('shrink-0', style.text)} aria-hidden="true" />
        <span className={cx('font-semibold', style.text)}>{title}</span>
        <span className={cx('rounded-full bg-surface/70 px-2 py-0.5 text-xs font-semibold tabular-nums', style.text)}>
          {items.length}
        </span>
        <span className="ml-auto flex items-center gap-1 text-[13px] font-medium text-muted">
          {open ? 'Hide' : 'Show'}
          <ChevronDown size={16} aria-hidden="true" className={cx('transition-transform duration-300', open ? 'rotate-180' : 'rotate-0')} />
        </span>
      </button>
      <Expand open={open}>
        <ul id={panel} className="space-y-2 px-4 pb-3.5 pl-11 text-sm text-ink">
          {items.map((item, index) => (
            <li key={index} className="relative before:absolute before:-left-3 before:top-[0.6em] before:size-1 before:rounded-full before:bg-current before:opacity-50">
              {item}
            </li>
          ))}
        </ul>
      </Expand>
    </div>
  );
}

const PILL_TONES = {
  neutral: 'bg-sunken text-muted',
  good: 'bg-good-soft text-good',
  warn: 'bg-warn-soft text-warn',
  bad: 'bg-bad-soft text-bad',
  info: 'bg-info-soft text-info',
  primary: 'bg-primary-soft text-primary',
};

export function Pill({ tone = 'neutral', icon: Icon, children, className }) {
  return (
    <span className={cx('pill', PILL_TONES[tone], className)}>
      {Icon && <Icon size={12} strokeWidth={2.5} aria-hidden="true" />}
      {children}
    </span>
  );
}

export function Tip({ text, children, className }) {
  return (
    <span className={cx('tip inline-flex', className)}>
      {children}
      <span role="tooltip" className="tip-body">{text}</span>
    </span>
  );
}

export function Skeleton({ className }) {
  return <div className={cx('skeleton', className)} aria-hidden="true" />;
}

export function EmptyState({ icon: Icon, title, children, action }) {
  return (
    <div className="rise mx-auto flex max-w-md flex-col items-center px-4 py-16 text-center">
      {Icon && (
        <div className="mb-4 grid size-14 place-items-center rounded-2xl bg-primary-soft text-primary">
          <Icon size={26} aria-hidden="true" />
        </div>
      )}
      <h2 className="text-xl">{title}</h2>
      {children && <p className="mt-2 text-muted">{children}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/** Opens and closes smoothly, and leaves hidden contents out of keyboard order. */
export function Expand({ open, children }) {
  return (
    <div className="expand" data-open={open} inert={!open || undefined}>
      <div>{children}</div>
    </div>
  );
}

export function Disclosure({ title, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded-md text-sm font-semibold text-muted transition-colors hover:text-ink"
      >
        <ChevronDown size={16} aria-hidden="true" className={cx('transition-transform duration-300', open ? 'rotate-0' : '-rotate-90')} />
        {title}
      </button>
      <Expand open={open}><div className="pt-3">{children}</div></Expand>
    </div>
  );
}

const prefersStill = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** A number that glides to its new value instead of jumping. */
export function CountUp({ value, decimals = 0, className }) {
  const [shown, setShown] = useState(value);
  const from = useRef(0);

  useEffect(() => {
    if (prefersStill()) {
      from.current = value;
      setShown(value);
      return undefined;
    }
    const start = from.current;
    const began = performance.now();
    let frame;
    const tick = (now) => {
      const t = Math.min(1, (now - began) / 550);
      const eased = 1 - (1 - t) ** 3;
      setShown(start + (value - start) * eased);
      if (t < 1) frame = requestAnimationFrame(tick);
      else from.current = value;
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      from.current = value;
    };
  }, [value]);

  return <span className={cx('tabular-nums', className)}>{Number(shown).toFixed(decimals)}</span>;
}

export function ProgressBar({ value = 0, tone = 'primary', className, label }) {
  const percent = Math.max(0, Math.min(100, value));
  const fill = { primary: 'bg-primary', good: 'bg-good', warn: 'bg-warn' }[tone];
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuenow={Math.round(percent)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cx('h-2 overflow-hidden rounded-full bg-sunken', className)}
    >
      <div
        className={cx('h-full w-full origin-left rounded-full transition-transform duration-500 ease-out', fill)}
        style={{ transform: `scaleX(${percent / 100})` }}
      />
    </div>
  );
}

export function ProgressRing({ value = 0, size = 64, children, label }) {
  const stroke = 6;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const percent = Math.max(0, Math.min(100, value));
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} role="img" aria-label={label}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--sunken)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--primary)"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - percent / 100)}
          style={{ transition: 'stroke-dashoffset 0.6s cubic-bezier(0.2, 0.8, 0.2, 1)' }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-sm font-bold tabular-nums">{children}</div>
    </div>
  );
}

/** Measures the active option, so a highlight can slide to it. Hidden pages measure again once shown. */
export function useSlidingThumb(activeKey) {
  const container = useRef(null);
  const [box, setBox] = useState(null);

  useLayoutEffect(() => {
    const el = container.current;
    if (!el) return undefined;
    const measure = () => {
      const active = el.querySelector('[data-active="true"]');
      if (!active || !active.offsetWidth) {
        setBox(null);
        return;
      }
      const outer = el.getBoundingClientRect();
      const inner = active.getBoundingClientRect();
      setBox({ x: inner.left - outer.left, y: inner.top - outer.top, width: inner.width, height: inner.height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [activeKey]);

  const style = box
    ? { width: box.width, height: box.height, transform: `translate(${box.x}px, ${box.y}px)` }
    : { opacity: 0 };
  return { container, style };
}

export function Segmented({ value, onChange, options, label }) {
  const { container, style } = useSlidingThumb(value);
  return (
    <div ref={container} role="group" aria-label={label} className="relative inline-flex rounded-[0.7rem] bg-sunken p-1">
      <span className="thumb rounded-[0.55rem] bg-surface shadow-soft" style={style} aria-hidden="true" />
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          data-active={option.value === value}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={cx(
            'relative whitespace-nowrap rounded-[0.55rem] px-3 py-1.5 text-[13px] font-semibold transition-colors duration-200',
            option.value === value ? 'text-ink' : 'text-muted hover:text-ink',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

const matchesAccept = (file, accept) => {
  if (!accept) return true;
  const name = file.name.toLowerCase();
  return accept.split(',').some((extension) => name.endsWith(extension.trim()));
};

/** Drag and drop, or click. The real file input stays in the page for keyboards and screen readers. */
export function Dropzone({
  title, hint, accept, multiple = false, required = false, disabled = false, files = [], onFiles, onReject, optional = false, compact = false,
}) {
  const id = useId();
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);
  const filled = files.length > 0 && !multiple;

  const take = (list) => {
    const all = Array.from(list || []);
    const good = all.filter((file) => matchesAccept(file, accept));
    if (good.length < all.length) {
      onReject?.(`Please use one of these file types: ${accept.replaceAll(',', ', ')}`);
    }
    if (good.length > 0) onFiles(multiple ? good : good.slice(0, 1));
  };

  return (
    <label
      htmlFor={id}
      className="dropzone"
      data-dragging={dragging}
      data-filled={filled}
      data-disabled={disabled}
      onDragEnter={(e) => { e.preventDefault(); depth.current += 1; if (!disabled) setDragging(true); }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={() => { depth.current -= 1; if (depth.current <= 0) { depth.current = 0; setDragging(false); } }}
      onDrop={(e) => {
        e.preventDefault();
        depth.current = 0;
        setDragging(false);
        if (!disabled) take(e.dataTransfer.files);
      }}
    >
      <input
        id={id}
        type="file"
        className="sr-only"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        required={required && files.length === 0}
        onChange={(e) => {
          take(e.target.files);
          e.target.value = '';
        }}
      />
      {filled ? (
        <div className={cx('flex items-center gap-3', compact ? 'p-3' : 'p-4')}>
          <div className="pop grid size-10 shrink-0 place-items-center rounded-xl bg-primary-soft text-primary">
            <FileText size={20} aria-hidden="true" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{files[0].name}</div>
            <div className="text-[13px] text-muted">{formatBytes(files[0].size)} · click to change</div>
          </div>
          <button
            type="button"
            className="icon-btn shrink-0"
            aria-label={`Remove ${files[0].name}`}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onFiles([]);
            }}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
      ) : (
        <div className={cx('flex flex-col items-center px-4 text-center', compact ? 'py-5' : 'py-9')}>
          <UploadCloud
            size={compact ? 24 : 32}
            aria-hidden="true"
            className={cx('mb-2 transition-transform duration-300', dragging ? '-translate-y-1 text-primary' : 'text-muted')}
          />
          <div className="text-sm font-semibold">
            {title}
            {required && <span className="text-bad"> *</span>}
            {optional && <span className="font-normal text-muted"> (optional)</span>}
          </div>
          <div className="mt-0.5 text-[13px] text-muted">
            {dragging ? 'Drop to add' : hint}
          </div>
        </div>
      )}
    </label>
  );
}
