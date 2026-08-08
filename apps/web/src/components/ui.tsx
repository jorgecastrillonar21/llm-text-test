import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from 'react';
import { useId } from 'react';

export function Button({
  variant = 'primary',
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'ghost' | 'subtle' }) {
  const styles = {
    primary:
      'bg-ember-500 text-ink-950 hover:bg-ember-400 disabled:bg-ink-700 disabled:text-ink-400',
    ghost: 'border border-ink-600 text-ink-50 hover:border-ink-400 disabled:text-ink-400',
    subtle: 'bg-ink-800 text-ink-50 hover:bg-ink-700 disabled:text-ink-400',
  }[variant];

  return (
    <button
      className={`rounded-lg px-4 py-2.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${styles} ${className}`}
      {...props}
    />
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-ink-200">
        {label}
      </label>
      {children(id)}
      {hint ? <p className="text-xs text-ink-400">{hint}</p> : null}
    </div>
  );
}

const controlStyles =
  'w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-base text-ink-50 placeholder:text-ink-400 focus:border-arcane-400 focus:outline-none';

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={controlStyles} {...props} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`${controlStyles} resize-y`} rows={3} {...props} />;
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-ink-700 bg-ink-850 p-4 ${className}`}>{children}</div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-xl border border-dashed border-ink-700 px-4 py-8 text-center text-sm text-ink-400">
      {children}
    </p>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <span className="flex items-center gap-2 text-sm text-ink-200" role="status">
      <span
        aria-hidden="true"
        className="size-3.5 animate-spin rounded-full border-2 border-ink-600 border-t-ember-500"
      />
      {label}
    </span>
  );
}
