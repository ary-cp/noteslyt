import { useTheme } from "../lib/theme";

/**
 * NotesLyt brand lockup.
 *
 * Mark (theme-aware): `02-app-icon-black.svg` on dark surfaces,
 * `03-app-icon-white.svg` on light surfaces. Pass `src` to pin a specific
 * file (e.g. `/05-app-icon-neon.svg`, `/04-app-icon-green.svg`).
 * Wordmark: "Notes" inherits the surrounding text color (white in dark
 * theme, black in light theme via `var(--text-primary)`), "lyt" is always
 * emerald (`#10b981`, matching the logo suite's `#14c46d` family).
 *
 * Full lockups (theme-paired): `01-primary-logo.svg` for dark surfaces and
 * `07-logo-light-bg.svg` (white card) for light surfaces.
 *
 * Full logo suite lives in `public/`:
 *   00-mark.svg                 transparent mark
 *   01-primary-logo.svg         full lockup (light surfaces)
 *   02/03/04-app-icon-*.svg    rounded app icons (black / white / green)
 *   05-app-icon-neon.svg       neon glow variant (dark surfaces)
 *   06-logo-dark-bg.svg         full lockup on dark card
 *   07-logo-light-bg.svg        full lockup on light card
 *   08/09/10-variant-*.svg     AI / open-book / stacked-book variants
 */

export const BRAND_EMERALD = "#10b981";
export const BRAND_MARK_SRC = "/04-app-icon-green.svg";
export const BRAND_MARK_DARK_SRC = "/02-app-icon-black.svg";
export const BRAND_MARK_LIGHT_SRC = "/03-app-icon-white.svg";
export const BRAND_LOCKUP_DARK_SRC = "/01-primary-logo.svg?v=2";
export const BRAND_LOCKUP_LIGHT_SRC = "/07-logo-light-bg.svg?v=2";

export function BrandMark({
  size = 28,
  rounded,
  alt = "NotesLyt",
  src,
  srcDark = BRAND_MARK_DARK_SRC,
  srcLight = BRAND_MARK_LIGHT_SRC,
}: {
  size?: number;
  rounded?: number;
  alt?: string;
  src?: string;
  srcDark?: string;
  srcLight?: string;
}) {
  const { resolved } = useTheme();
  const finalSrc = src ?? (resolved === "dark" ? srcDark : srcLight);
  return (
    <img
      src={finalSrc}
      alt={alt}
      width={size}
      height={size}
      style={{
        width: size,
        height: size,
        borderRadius: rounded ?? Math.max(5, Math.round(size / 4)),
        flexShrink: 0,
        boxShadow: "0 0 0 1px var(--border)",
      }}
      draggable={false}
    />
  );
}

export function BrandWordmark({ fontSize = 15 }: { fontSize?: number }) {
  return (
    <span
      aria-hidden="true"
      style={{
        fontSize,
        fontWeight: 700,
        letterSpacing: "-0.02em",
        lineHeight: 1,
        whiteSpace: "nowrap",
      }}
    >
      <span style={{ color: "var(--text-primary)" }}>Notes</span>
      <span style={{ color: BRAND_EMERALD }}>lyt</span>
    </span>
  );
}

export default function Brand({
  markSize = 28,
  fontSize = 15,
  gap = 8,
  wordmark = true,
}: {
  markSize?: number;
  fontSize?: number;
  gap?: number;
  wordmark?: boolean;
}) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap }}>
      <BrandMark size={markSize} rounded={Math.max(5, Math.round(markSize / 4))} />
      {wordmark && <BrandWordmark fontSize={fontSize} />}
    </span>
  );
}
