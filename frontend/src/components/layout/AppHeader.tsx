import { Link, useNavigate } from "react-router-dom";
import { BookMarked, LogOut, Moon, Search, Settings as SettingsIcon, Sun } from "lucide-react";
import { useAuth } from "../../lib/auth";
import { useTheme } from "../../lib/theme";
import { IconButton } from "../ui/Button";

export function AppHeader({ onSearch, onSettings }: { onSearch: () => void; onSettings: () => void }) {
  const { resolved, toggle } = useTheme();
  const { user, signOut } = useAuth();
  const navigate = useNavigate();

  return (
    <header className="flex h-[52px] shrink-0 items-center justify-between gap-3 border-b border-[var(--border)] bg-[var(--sidebar-bg)] px-4">
      <Link to="/" className="flex min-w-0 items-center gap-2" aria-label="StudySpace home">
        <span className="flex h-7 w-7 items-center justify-center rounded-[7px] bg-[var(--accent)] text-white">
          <BookMarked size={15} />
        </span>
        <span className="text-[15px] font-semibold tracking-tight">StudySpace</span>
      </Link>
      <div className="flex items-center gap-1">
        <IconButton label="Search (Ctrl+K)" onClick={onSearch}>
          <Search size={16} />
        </IconButton>
        <IconButton label={resolved === "dark" ? "Switch to light mode" : "Switch to dark mode"} onClick={toggle}>
          {resolved === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </IconButton>
        <IconButton label="Settings" onClick={onSettings}>
          <SettingsIcon size={16} />
        </IconButton>
        {user ? (
          <span className="ml-1 flex items-center gap-2">
            <span
              className="hidden h-7 w-7 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface-secondary)] text-xs font-semibold sm:inline-flex"
              title={user.email}
            >
              {user.display_name.slice(0, 1).toUpperCase()}
            </span>
            <IconButton
              label="Sign out"
              onClick={() => {
                signOut();
                navigate("/login");
              }}
            >
              <LogOut size={16} />
            </IconButton>
          </span>
        ) : (
          <Link
            to="/login"
            className="ml-1 inline-flex h-8 items-center rounded-[7px] bg-[var(--accent)] px-3 text-[13px] font-medium text-white hover:bg-[var(--accent-hover)]"
          >
            Sign in
          </Link>
        )}
      </div>
    </header>
  );
}
