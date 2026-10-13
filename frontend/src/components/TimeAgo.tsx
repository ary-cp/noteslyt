import { formatAbsolute, useTimeAgo } from "../lib/utils";

export function TimeAgo({ iso, className }: { iso: string; className?: string }) {
  const label = useTimeAgo(iso);
  if (!label) return null;
  return (
    <span className={className} title={formatAbsolute(iso)}>
      {label}
    </span>
  );
}
