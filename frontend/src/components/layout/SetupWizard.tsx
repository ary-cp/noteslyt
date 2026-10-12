import { useEffect, useRef, useState } from "react";
import { completeSetup, fetchModels, getAdminSettings, patchAdminSettings } from "../../lib/api";
import type { SettingItem } from "../../lib/types";
import { useToast } from "../../lib/toast";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";
import { Dropdown } from "../ui/Menu";
import { Field, TextInput } from "../ui/Controls";

const CHAT_PRESETS: { value: string; label: string; base_url: string; model: string; key: string }[] = [
  { value: "custom", label: "Custom endpoint", base_url: "", model: "", key: "" },
  { value: "lmstudio", label: "LM Studio (local)", base_url: "http://localhost:1234/v1", model: "qwen2.5-7b-instruct", key: "lm-studio" },
  { value: "groq", label: "Groq (cloud)", base_url: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile", key: "" },
  { value: "openai", label: "OpenAI", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini", key: "" },
];

const STEPS = ["Chat", "Embeddings", "Transcription"] as const;

function itemOf(items: SettingItem[], key: string): SettingItem | undefined {
  return items.find((i) => i.key === key);
}function strVal(item: SettingItem | undefined): string {
  if (!item || item.secret) return "";
  return item.value === null || item.value === undefined ? "" : String(item.value);
}

/**
 * Model id input with an auto-fetched picker: typing (or picking a preset
 * that fills) the base URL queries the endpoint's `/models` after a pause,
 * and the field becomes a dropdown. "Custom…" and the refresh link keep
 * just-released ids and unreachable endpoints working.
 */
function ModelField({
  baseUrl, apiKey, value, onChange, disabled, placeholder,
}: {
  baseUrl: string;
  apiKey: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const label = "Model";
  const [options, setOptions] = useState<string[] | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState("");
  const [custom, setCustom] = useState(false);
  const lastFetched = useRef("");

  const fetch = async (base: string, key: string) => {
    const trimmed = base.trim();
    if (!trimmed || disabled) return;
    lastFetched.current = trimmed;
    setFetching(true);
    setFetchError("");
    try {
      const res = await fetchModels(trimmed, key.trim() || undefined);
      const ids = res.models.map((m) => m.id);
      setOptions(ids);
      if (ids.length === 0) setFetchError("Endpoint returned no models — type the id manually.");
    } catch (e) {
      setOptions(null);
      setFetchError(e instanceof Error ? e.message : String(e));
    } finally {
      setFetching(false);
    }
  };

  useEffect(() => {
    const trimmed = baseUrl.trim();
    if (!trimmed || disabled || (trimmed === lastFetched.current && options !== null)) return;
    const timer = window.setTimeout(() => void fetch(trimmed, apiKey), 800);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, apiKey, disabled]);

  const showList = options !== null && !custom;
  const dropdownOptions = showList
    ? [...(options ?? []), ...(value.trim() && !(options ?? []).includes(value.trim()) ? [value.trim()] : []), "__custom__"]
    : [];

  return (
    <>
      {showList ? (
        <div className="flex items-center gap-2">
          <Dropdown
            value={dropdownOptions.includes(value) ? value : ""}
            ariaLabel={label}
            className="min-w-0 flex-1"
            placeholder={options?.length ? "Pick a model…" : "No models found"}
            onChange={(v) => {
              if (v === "__custom__") setCustom(true);
              else onChange(v);
            }}
            options={dropdownOptions.map((id) => id === "__custom__" ? { value: id, label: "Custom… (type manually)" } : { value: id, label: id })}
          />
          <Button variant="ghost" disabled={fetching || disabled} onClick={() => void fetch(baseUrl, apiKey)}>
            {fetching ? "…" : "Refresh"}
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <TextInput
            value={value}
            disabled={disabled}
            aria-label={label}
            placeholder={options !== null && !custom ? "Pick from the list or…" : placeholder}
            onChange={(e) => onChange(e.target.value)}
          />
          {options !== null && (
            <Button variant="ghost" disabled={disabled} onClick={() => setCustom(false)}>
              List
            </Button>
          )}
        </div>
      )}
      {fetching && <p className="mt-1 text-xs text-[var(--text-muted)]">Listing models…</p>}
      {!fetching && fetchError && (
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          {fetchError}{" "}
          <button type="button" onClick={() => void fetch(baseUrl, apiKey)} className="cursor-pointer underline hover:text-[var(--text-primary)]">
            Retry
          </button>
        </p>
      )}
    </>
  );
}

/**
 * First-run onboarding: right after the first account is created, the admin
 * enters the API keys/endpoints needed to actually use the app, then lands
 * on a working dashboard. Finish and Skip both mark setup complete (the
 * choice is re-editable under Settings); closing the dialog keeps it pending
 * so it reappears next sign-in.
 */
export function SetupWizard({ open, onDone }: { open: boolean; onDone: () => void }) {
  const { push } = useToast();
  const [step, setStep] = useState(0);
  const [items, setItems] = useState<SettingItem[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [preset, setPreset] = useState("custom");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setStep(0);
    setError("");
    setLoadError("");
    getAdminSettings()
      .then((s) => {
        setItems(s.settings);
        const initial: Record<string, string> = {};
        for (const item of s.settings) {
          if (!item.secret && typeof item.value !== "boolean") {
            initial[item.key] = item.value === null || item.value === undefined ? "" : String(item.value);
          }
        }
        setDrafts(initial);
      })
      .catch((e) => setLoadError(e instanceof Error ? e.message : "Could not load server settings"));
  }, [open ]);

  if (!open) return null;

  const locked = (key: string) => itemOf(items ?? [], key)?.locked ?? false;
  const get = (key: string) => drafts[key] ?? "";
  const set = (key: string, value: string) => setDrafts((prev) => ({ ...prev, [key]: value }));

  const applyPreset = (value: string) => {
    setPreset(value);
    const found = CHAT_PRESETS.find((p) => p.value === value);
    if (!found || value === "custom") return;
    if (!locked("llm_base_url")) set("llm_base_url", found.base_url);
    if (!locked("llm_model")) set("llm_model", found.model);
    if (!locked("llm_api_key") && found.key) set("llm_api_key", found.key);
  };

  const require = (key: string, label: string): string | null => {
    if (locked(key)) return null;
    if (get(key).trim() === "") {
      setError(`${label} is required.`);
      return label;
    }
    return null;
  };

  const validate = (): boolean => {
    setError("");
    if (require("llm_base_url", "Chat base URL")) return false;
    if (require("llm_api_key", "Chat API key")) return false;
    if (require("llm_model", "Chat model")) return false;
    if ((get("embedding_mode") || "cloud") === "cloud") {
      if (require("embedding_base_url", "Embedding base URL")) return false;
      if (require("embedding_model", "Embedding model")) return false;
    }
    if ((get("transcription_mode") || "cloud") === "cloud") {
      if (require("transcription_base_url", "Transcription base URL")) return false;
      if (require("transcription_model", "Transcription model")) return false;
    }
    return true;
  };

  const collect = (): Record<string, unknown> => {
    const body: Record<string, unknown> = {};
    const consider = (key: string) => {
      const item = itemOf(items ?? [], key);
      if (!item || item.locked) return;
      if (item.secret) {
        if ((drafts[key] ?? "") !== "") body[key] = drafts[key];
        return;
      }
      const loaded = strVal(item);
      if ((drafts[key] ?? "") !== loaded) body[key] = drafts[key] ?? "";
    };
    for (const key of [
      "llm_base_url", "llm_api_key", "llm_model",
      "embedding_mode", "embedding_base_url", "embedding_api_key", "embedding_model",
      "transcription_mode", "transcription_base_url", "transcription_api_key", "transcription_model",
    ]) consider(key);
    return body;
  };

  const finish = async () => {
    if (!validate()) return;
    setSaving(true);
    setError("");
    try {
      const body = collect();
      if (Object.keys(body).length > 0) await patchAdminSettings(body);
      await completeSetup();
      push("You're all set", { detail: "Settings live under Settings if you ever need them.", tone: "success" });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const skip = async () => {
    setSaving(true);
    try {
      await completeSetup();
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const secretPlaceholder = (key: string) => {
    const item = itemOf(items ?? [], key);
    return item?.configured ? "•••••• (saved — type to replace)" : "Not set yet";
  };

  const embeddingMode = get("embedding_mode") || "cloud";
  const transcriptionMode = get("transcription_mode") || "cloud";

  return (
    <Modal open={open} onClose={() => undefined} title="Welcome to NotesLyt" subtitle="Connect your AI providers to get started." width={620}>
      <div className="grid gap-5">
        <div className="flex items-center gap-2">
          {STEPS.map((label, i) => (
            <div key={label} className="flex flex-1 items-center gap-2">
              <div
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-medium ${
                  i === step ? "bg-[var(--accent)] text-white" : i < step ? "bg-[var(--surface-secondary)] text-[var(--text-primary)]" : "bg-[var(--surface-secondary)] text-[var(--text-muted)]"
                }`}
              >
                {i + 1}
              </div>
              <span className={`text-[13px] ${i === step ? "font-medium text-[var(--text-primary)]" : "text-[var(--text-muted)]"}`}>{label}</span>
              {i < STEPS.length - 1 && <div className="h-px flex-1 bg-[var(--border)]" />}
            </div>
          ))}
        </div>

        {loadError ? (
          <p role="alert" className="text-sm text-[var(--danger)]">{loadError}</p>
        ) : items === null ? (
          <p className="text-sm text-[var(--text-muted)]">Loading server settings…</p>
        ) : step === 0 ? (
          <div className="grid gap-3">
            <Field label="Provider preset">
              <Dropdown value={preset} ariaLabel="Provider preset" onChange={applyPreset} options={CHAT_PRESETS.map((p) => ({ value: p.value, label: p.label }))} />
            </Field>
            <Field label="Base URL" hint={locked("llm_base_url") ? "Set by the server operator." : undefined}>
              <TextInput value={get("llm_base_url")} disabled={locked("llm_base_url")} aria-label="Chat base URL" placeholder="https://api.openai.com/v1" onChange={(e) => set("llm_base_url", e.target.value)} />
            </Field>
            <Field label="API key" hint={locked("llm_api_key") ? "Set by the server operator." : undefined}>
              <TextInput type="password" value={get("llm_api_key")} disabled={locked("llm_api_key")} aria-label="Chat API key" placeholder={secretPlaceholder("llm_api_key")} onChange={(e) => set("llm_api_key", e.target.value)} />
            </Field>
            <Field label="Model" hint={locked("llm_model") ? "Set by the server operator." : "Pick from the endpoint, or type any id."}>
              <ModelField
                baseUrl={get("llm_base_url")}
                apiKey={get("llm_api_key")}
                value={get("llm_model")}
                onChange={(v) => set("llm_model", v)}
                disabled={locked("llm_model")}
                placeholder="gpt-4o-mini"
              />
            </Field>
          </div>
        ) : step === 1 ? (
          <div className="grid gap-3">
            <Field label="Mode">
              <Dropdown
                value={embeddingMode}
                ariaLabel="Embedding mode"
                onChange={(v) => { if (!locked("embedding_mode")) set("embedding_mode", v); }}
                options={[
                  { value: "local", label: "Local server (automatic download)", disabled: locked("embedding_mode") },
                  { value: "cloud", label: "Cloud endpoint", disabled: locked("embedding_mode") },
                ]}
              />
            </Field>
            {embeddingMode === "local" ? (
              <p className="text-[13px] text-[var(--text-muted)]">The embedding engine downloads itself on first use — nothing to enter here.</p>
            ) : (
              <>
                <Field label="Base URL" hint={locked("embedding_base_url") ? "Set by the server operator." : undefined}>
                  <TextInput value={get("embedding_base_url")} disabled={locked("embedding_base_url")} aria-label="Embedding base URL" placeholder="https://api.openai.com/v1" onChange={(e) => set("embedding_base_url", e.target.value)} />
                </Field>
                <Field label="API key" hint={locked("embedding_api_key") ? "Set by the server operator." : undefined}>
                  <TextInput type="password" value={get("embedding_api_key")} disabled={locked("embedding_api_key")} aria-label="Embedding API key" placeholder={secretPlaceholder("embedding_api_key")} onChange={(e) => set("embedding_api_key", e.target.value)} />
                </Field>
                <Field label="Model" hint={locked("embedding_model") ? "Set by the server operator." : "Pick from the endpoint, or type any id."}>
                  <ModelField
                    baseUrl={get("embedding_base_url")}
                    apiKey={get("embedding_api_key")}
                    value={get("embedding_model")}
                    onChange={(v) => set("embedding_model", v)}
                    disabled={locked("embedding_model")}
                    placeholder="text-embedding-3-small"
                  />
                </Field>
              </>
            )}
          </div>
        ) : (
          <div className="grid gap-3">
            <Field label="Mode">
              <Dropdown
                value={transcriptionMode}
                ariaLabel="Transcription mode"
                onChange={(v) => { if (!locked("transcription_mode")) set("transcription_mode", v); }}
                options={[
                  { value: "cloud", label: "Cloud endpoint", disabled: locked("transcription_mode") },
                  { value: "local", label: "Local server (automatic download)", disabled: locked("transcription_mode") },
                ]}
              />
            </Field>
            {transcriptionMode === "local" ? (
              <p className="text-[13px] text-[var(--text-muted)]">The speech-to-text engine downloads itself on first use — nothing to enter here.</p>
            ) : (
              <>
                <Field label="Base URL" hint={locked("transcription_base_url") ? "Set by the server operator." : undefined}>
                  <TextInput value={get("transcription_base_url")} disabled={locked("transcription_base_url")} aria-label="Transcription base URL" placeholder="https://api.groq.com/openai/v1" onChange={(e) => set("transcription_base_url", e.target.value)} />
                </Field>
                <Field label="API key" hint={locked("transcription_api_key") ? "Set by the server operator." : undefined}>
                  <TextInput type="password" value={get("transcription_api_key")} disabled={locked("transcription_api_key")} aria-label="Transcription API key" placeholder={secretPlaceholder("transcription_api_key")} onChange={(e) => set("transcription_api_key", e.target.value)} />
                </Field>
                <Field label="Model" hint={locked("transcription_model") ? "Set by the server operator." : "Pick from the endpoint, or type any id."}>
                  <ModelField
                    baseUrl={get("transcription_base_url")}
                    apiKey={get("transcription_api_key")}
                    value={get("transcription_model")}
                    onChange={(v) => set("transcription_model", v)}
                    disabled={locked("transcription_model")}
                    placeholder="whisper-large-v3-turbo"
                  />
                </Field>
              </>
            )}
          </div>
        )}

        {error && (
          <p role="alert" className="text-sm text-[var(--danger)]">{error}</p>
        )}

        <div className="flex items-center justify-between gap-2">
          <div className="flex gap-2">
            {step > 0 && (
              <Button variant="ghost" disabled={saving} onClick={() => { setError(""); setStep(step - 1); }}>Back</Button>
            )}
            <Button variant="ghost" disabled={saving} onClick={() => void skip()}>Skip for now</Button>
          </div>
          {step < STEPS.length - 1 ? (
            <Button variant="primary" disabled={saving || items === null} onClick={() => { setError(""); setStep(step + 1); }}>Next</Button>
          ) : (
            <Button variant="primary" loading={saving} disabled={saving || items === null} onClick={() => void finish()}>Finish</Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
