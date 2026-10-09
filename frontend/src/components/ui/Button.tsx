import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "sm" | "md" | "xs";

const variants: Record<Variant, string> = {
  primary:
    "bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)] border border-transparent disabled:opacity-50",
  secondary:
    "bg-[var(--surface-secondary)] text-[var(--text-primary)] border border-[var(--border)] hover:border-[var(--border-strong)] disabled:opacity-50",
  outline:
    "bg-transparent text-[var(--text-primary)] border border-[var(--border)] hover:bg-[var(--surface-secondary)] disabled:opacity-50",
  ghost: "bg-transparent text-[var(--text-secondary)] border border-transparent hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)] disabled:opacity-50",
  danger:
    "bg-transparent text-[var(--danger)] border border-[var(--border)] hover:bg-[var(--danger-soft)] disabled:opacity-50",
};

const sizes: Record<Size, string> = {
  xs: "h-7 px-2.5 text-xs",
  sm: "h-8 px-3 text-[13px]",
  md: "h-9 px-4 text-sm",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export function Button({ variant = "secondary", size = "sm", loading, className = "", children, disabled, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      className={`inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-[7px] font-medium whitespace-nowrap transition-[background-color,border-color,color] duration-150 disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${className}`}
      {...rest}
    >
      {loading && <Spinner size={13} />}
      {children}
    </button>
  );
}

export function IconButton({
  label,
  className = "",
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      data-tooltip={label}
      className={`tooltip-host inline-flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-[7px] border border-transparent text-[var(--text-secondary)] transition-colors duration-150 hover:bg-[var(--surface-secondary)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40 ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <span
      aria-hidden
      className="inline-block animate-spin rounded-full border-2 border-current border-t-transparent"
      style={{ width: size, height: size, opacity: 0.8 }}
    />
  );
}
