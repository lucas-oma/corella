import { useEffect, useRef, useState } from "react";

import AppShell from "@/components/AppShell";
import { ApiError, api, type KBDocument } from "@/lib/api";

const POLL_INTERVAL_MS = 3000;

const STATUS_LABEL: Record<KBDocument["status"], string> = {
  pending: "Pending",
  processing: "Processing",
  ready: "Ready",
  failed: "Failed",
};

const STATUS_CLASS: Record<KBDocument["status"], string> = {
  pending: "border-border text-ink-muted dark:border-border-dark",
  processing: "border-border text-ink-muted dark:border-border-dark",
  ready: "border-status-success/30 text-status-success",
  failed: "border-status-danger/30 text-status-danger",
};

export default function KnowledgeBase() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [documents, setDocuments] = useState<KBDocument[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    api.listKBDocuments().then(setDocuments);
  }, []);

  // Poll while anything is still pending/processing, so status/chunk counts
  // update without a manual refresh.
  useEffect(() => {
    if (!documents?.some((d) => d.status === "pending" || d.status === "processing")) return;
    const timer = setTimeout(() => {
      api.listKBDocuments().then(setDocuments);
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [documents]);

  async function onFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;

    setError(null);
    setUploading(true);
    try {
      const doc = await api.uploadKBDocument(file);
      setDocuments((prev) => [doc, ...(prev ?? [])]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function onDelete(id: string) {
    setDeletingId(id);
    try {
      await api.deleteKBDocument(id);
      setDocuments((prev) => prev?.filter((d) => d.id !== id) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't delete document");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <AppShell>
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="font-serif text-2xl text-ink dark:text-ink-inverted">Knowledge base</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Documents the copilot will ground its answers in, once it exists. PDF, Markdown, or
            plain text.
          </p>
        </div>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.md,.markdown,.txt,application/pdf,text/plain,text/markdown"
            className="hidden"
            onChange={onFileSelected}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="btn-primary"
          >
            {uploading ? "Uploading…" : "Upload document"}
          </button>
        </div>
      </div>

      {error && <p className="mb-4 text-sm text-status-danger">{error}</p>}

      {documents === null && <p className="text-sm text-ink-muted">Loading…</p>}

      {documents?.length === 0 && (
        <div className="card p-10 text-center">
          <p className="text-sm text-ink-muted">
            No documents yet. Upload one to start building your knowledge base.
          </p>
        </div>
      )}

      {documents && documents.length > 0 && (
        <ul className="card divide-y divide-border dark:divide-border-dark">
          {documents.map((doc) => (
            <li key={doc.id} className="flex items-center justify-between px-5 py-4">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink dark:text-ink-inverted">
                  {doc.filename}
                </p>
                <p className="mt-0.5 text-xs text-ink-subtle">
                  {new Date(doc.created_at).toLocaleString()}
                  {doc.status === "ready" && doc.chunk_count !== null && (
                    <> · {doc.chunk_count} chunk{doc.chunk_count === 1 ? "" : "s"}</>
                  )}
                </p>
                {doc.status === "failed" && doc.error && (
                  <p className="mt-1 text-xs text-status-danger">{doc.error}</p>
                )}
              </div>
              <div className="ml-4 flex shrink-0 items-center gap-3">
                <span
                  className={`rounded-sm border px-2 py-0.5 text-xs ${STATUS_CLASS[doc.status]}`}
                >
                  {STATUS_LABEL[doc.status]}
                </span>
                <button
                  onClick={() => onDelete(doc.id)}
                  disabled={deletingId === doc.id}
                  className="text-xs text-ink-subtle hover:text-status-danger"
                >
                  {deletingId === doc.id ? "…" : "Delete"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
