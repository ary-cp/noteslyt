/**
 * Document registry state: which PDFs exist, which one is open, uploading.
 * On first load we re-open the most recent document so a page refresh never
 * loses the demo.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, deleteDocument, listDocuments, uploadPdf } from "../lib/api";
import type { DocumentInfo } from "../lib/types";

export function useDocument() {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [doc, setDoc] = useState<DocumentInfo | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [booted, setBooted] = useState(false);

  useEffect(() => {
    listDocuments()
      .then((docs) => {
        setDocuments(docs);
        if (docs[0]) setDoc(docs[0]);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setBooted(true));
  }, []);

  const upload = useCallback(async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      const created = await uploadPdf(file);
      setDocuments((prev) => [created, ...prev]);
      setDoc(created);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }, []);

  const remove = useCallback(
    async (id: string) => {
      try {
        await deleteDocument(id);
        setDocuments((prev) => {
          const next = prev.filter((d) => d.id !== id);
          if (doc?.id === id) setDoc(next[0] ?? null);
          return next;
        });
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Delete failed");
      }
    },
    [doc?.id],
  );

  return { documents, doc, select: setDoc, upload, remove, uploading, error, booted };
}
