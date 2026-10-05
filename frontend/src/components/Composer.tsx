/**
 * Message input. Enter sends, Shift+Enter inserts a newline, the textarea
 * grows up to ~6 lines. Deliberately plain: one border, one accent button.
 */

import { ArrowUp } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

interface Props {
  disabled?: boolean;
  placeholder?: string;
  onSend: (text: string) => void;
}

export function Composer({ disabled, placeholder = "Ask about your slides…", onSend }: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-grow
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

  const submit = useCallback(() => {
    const text = value.trim();
    if (!text || disabled) return;
    onSend(text);
    setValue("");
  }, [value, disabled, onSend]);

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="border-t border-line bg-surface px-4 py-3">
      <div className="flex items-end gap-2 rounded-2xl border border-line bg-surface px-3 py-2 shadow-[0_1px_2px_rgba(0,0,0,0.03)] focus-within:border-ink/30 transition-colors">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          className="flex-1 resize-none bg-transparent px-1 py-1.5 text-[15px] leading-6 outline-none placeholder:text-ink-muted/70 disabled:opacity-60"
        />
        <button
          type="button"
          onClick={submit}
          disabled={disabled || !value.trim()}
          aria-label="Send"
          className="mb-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink text-surface transition-opacity disabled:opacity-25 hover:opacity-90"
        >
          <ArrowUp size={16} strokeWidth={2.25} />
        </button>
      </div>
      <p className="mt-2 text-center text-[11px] text-ink-muted/80">
        Answers come only from your uploaded material and cite the page they are based on.
      </p>
    </div>
  );
}
