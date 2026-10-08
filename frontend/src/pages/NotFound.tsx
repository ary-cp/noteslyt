import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="flex min-h-full flex-col items-center justify-center px-6 py-16 text-center">
      <p className="font-mono text-[13px] text-[var(--text-muted)]">404</p>
      <h1 className="mt-2 text-[24px] font-semibold tracking-tight">This page isn't in this notebook.</h1>
      <p className="mt-1.5 max-w-[360px] text-sm text-[var(--text-secondary)]">
        The page you're looking for doesn't exist or was moved.
      </p>
      <div className="mt-5 flex gap-2">
        <Link to="/projects" className="inline-flex h-9 items-center rounded-[7px] bg-[var(--accent)] px-4 text-sm font-medium text-white hover:bg-[var(--accent-hover)]">
          Back to projects
        </Link>
        <Link to="/" className="inline-flex h-9 items-center rounded-[7px] border border-[var(--border)] px-4 text-sm font-medium hover:bg-[var(--surface-secondary)]">
          Go home
        </Link>
      </div>
    </div>
  );
}
