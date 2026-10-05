/**
 * Split-screen shell.
 *
 *   ┌──────────────────────────────┬──────────────────────────────┐
 *   │ TopBar  (doc · Chat|Quiz · +) │  PdfViewer toolbar           │
 *   ├──────────────────────────────┤                              │
 *   │ ChatPane   or   QuizPanel    │  continuous PDF pages        │
 *   │  …"[Page 14]" chip ──────────┼─▶ scrolls + flashes page 14  │
 *   └──────────────────────────────┴──────────────────────────────┘
 *
 * Both left panels stay mounted (toggled with `hidden`) so switching modes
 * never loses the conversation or the quiz in progress.
 */

import { useCallback, useMemo, useState } from "react";
import { ChatPane } from "./components/ChatPane";
import { PdfViewer, type PageTarget } from "./components/PdfViewer";
import { QuizPanel } from "./components/QuizPanel";
import { TopBar, type Mode } from "./components/TopBar";
import { UploadDropzone } from "./components/UploadDropzone";
import { useChat } from "./hooks/useChat";
import { useDocument } from "./hooks/useDocument";
import { useQuiz } from "./hooks/useQuiz";
import { documentFileUrl } from "./lib/api";
import { getUserId } from "./lib/user";

export default function App() {
  const userId = useMemo(getUserId, []);
  const { doc, documents, select, upload, remove, uploading, error, booted } = useDocument();
  const chat = useChat(doc?.id ?? null);
  const quiz = useQuiz(userId, doc?.id ?? null);

  const [mode, setMode] = useState<Mode>("chat");
  const [target, setTarget] = useState<PageTarget | null>(null);

  // The citation bridge: any "[Page N]" click anywhere in the left pane lands here.
  const goToPage = useCallback((page: number) => setTarget({ page, nonce: Date.now() }), []);

  if (!booted) return null;
  if (!doc) return <UploadDropzone uploading={uploading} error={error} onUpload={upload} />;

  return (
    <div className="flex h-screen flex-col bg-surface text-ink md:flex-row">
      {/* Left: tutor ------------------------------------------------------- */}
      <section className="flex h-1/2 min-h-0 flex-col border-b border-line md:h-full md:w-[46%] md:min-w-[400px] md:max-w-[720px] md:border-b-0 md:border-r">
        <TopBar
          doc={doc}
          documents={documents}
          mode={mode}
          uploading={uploading}
          onMode={setMode}
          onSelect={select}
          onUpload={upload}
          onDelete={remove}
        />
        {error && <p className="border-b border-line bg-surface-muted px-4 py-2 text-xs text-ink-muted">{error}</p>}
        <div className={`flex min-h-0 flex-1 flex-col ${mode === "chat" ? "" : "hidden"}`}>
          <ChatPane doc={doc} messages={chat.messages} busy={chat.busy} onSend={chat.send} onCite={goToPage} />
        </div>
        <div className={`flex min-h-0 flex-1 flex-col ${mode === "quiz" ? "" : "hidden"}`}>
          <QuizPanel doc={doc} quiz={quiz} onCite={goToPage} />
        </div>
      </section>

      {/* Right: source material ------------------------------------------- */}
      <section className="h-1/2 min-h-0 min-w-0 flex-1 md:h-full">
        <PdfViewer fileUrl={documentFileUrl(doc.id)} target={target} />
      </section>
    </div>
  );
}
