import { useEffect, useMemo, useRef, useState } from "react";

import AppShell from "@/components/AppShell";
import KBUploadModal from "@/components/KBUploadModal";
import { ApiError, api, type Group, type KBDocument } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useConfirm } from "@/lib/confirm";

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
  const { user } = useAuth();
  const confirm = useConfirm();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pendingGroupId = useRef<string | null>(null);
  const [documents, setDocuments] = useState<KBDocument[] | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [filter, setFilter] = useState<string>("all");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const isAdmin = user?.role === "admin";

  useEffect(() => {
    api.listKBDocuments().then(setDocuments);
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    api.adminListGroups().then(setGroups).catch(() => setGroups([]));
  }, [isAdmin]);

  useEffect(() => {
    if (!documents?.some((d) => d.status === "pending" || d.status === "processing")) return;
    const timer = setTimeout(() => {
      api.listKBDocuments().then(setDocuments);
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [documents]);

  const visible = useMemo(() => {
    if (!documents) return null;
    if (!isAdmin || filter === "all") return documents;
    if (filter === "unassigned") return documents.filter((d) => !d.group_id);
    return documents.filter((d) => d.group_id === filter);
  }, [documents, filter, isAdmin]);

  function startUpload(groupId: string | null) {
    pendingGroupId.current = groupId;
    setPickerOpen(false);
    fileInputRef.current?.click();
  }

  async function onFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;

    setError(null);
    setUploading(true);
    try {
      const doc = await api.uploadKBDocument(file, pendingGroupId.current);
      setDocuments((prev) => [doc, ...(prev ?? [])]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      pendingGroupId.current = null;
    }
  }

  async function onDelete(id: string) {
    const ok = await confirm({
      title: "Delete this document?",
      description: "The file and its indexed chunks will be removed. This can't be undone.",
      confirmLabel: "Delete document",
      variant: "danger",
    });
    if (!ok) return;
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
            {isAdmin
              ? "Documents the copilot grounds its answers in. Only admins can add or remove them — assign a document to a group so that group shares it."
              : "Documents your group's copilot grounds its answers in. An admin maintains this knowledge base."}
          </p>
        </div>
        {isAdmin && (
          <div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.md,.markdown,.txt,application/pdf,text/plain,text/markdown"
              className="hidden"
              onChange={onFileSelected}
            />
            <button
              onClick={() => setPickerOpen(true)}
              disabled={uploading}
              className="btn-primary"
            >
              {uploading ? "Uploading…" : "Upload document"}
            </button>
          </div>
        )}
      </div>

      {isAdmin && (groups.length > 0 || documents?.some((d) => !d.group_id)) && (
        <div className="mb-6 flex gap-1 overflow-x-auto border-b border-border dark:border-border-dark">
          {[
            { id: "all", label: "All" },
            { id: "unassigned", label: "Unassigned" },
            ...groups.map((g) => ({ id: g.id, label: g.name })),
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setFilter(tab.id)}
              className={`shrink-0 px-3 py-2 text-sm ${
                filter === tab.id
                  ? "border-b-2 border-accent text-ink dark:text-ink-inverted"
                  : "text-ink-muted hover:text-ink dark:hover:text-ink-inverted"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}

      {error && <p className="mb-4 text-sm text-status-danger">{error}</p>}

      {visible === null && <p className="text-sm text-ink-muted">Loading…</p>}

      {visible?.length === 0 && (
        <div className="card p-10 text-center">
          <p className="text-sm text-ink-muted">
            {isAdmin
              ? "No documents in this view. Upload one to start building a knowledge base."
              : "No documents yet. An admin can add them from this page."}
          </p>
        </div>
      )}

      {visible && visible.length > 0 && (
        <ul className="card divide-y divide-border dark:divide-border-dark">
          {visible.map((doc) => (
            <li key={doc.id} className="flex items-center justify-between px-5 py-4">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink dark:text-ink-inverted">
                  {doc.filename}
                </p>
                <p className="mt-0.5 text-xs text-ink-subtle">
                  {doc.group_name ?? (isAdmin ? "Unassigned" : null)}
                  {(doc.group_name || isAdmin) && " · "}
                  {doc.owner_name}
                  {" · "}
                  {new Date(doc.created_at).toLocaleString()}
                  {doc.status === "ready" && doc.chunk_count !== null && (
                    <> · {doc.chunk_count} chunk{doc.chunk_count === 1 ? "" : "s"}</>
                  )}
                </p>
                {doc.status === "failed" && doc.error && (
                  <p className="mt-1 text-xs text-status-danger">{doc.error}</p>
                )}
                {doc.status === "ready" && doc.keywords && doc.keywords.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {doc.keywords.map((keyword) => (
                      <span
                        key={keyword}
                        className="rounded-full bg-black/[0.03] px-2.5 py-0.5 text-xs text-ink-muted dark:bg-white/[0.06]"
                        title="Boosts transcription accuracy for this term"
                      >
                        {keyword}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <div className="ml-4 flex shrink-0 items-center gap-3">
                <span
                  className={`rounded-sm border px-2 py-0.5 text-xs ${STATUS_CLASS[doc.status]}`}
                >
                  {STATUS_LABEL[doc.status]}
                </span>
                {isAdmin && (
                  <button
                    onClick={() => onDelete(doc.id)}
                    disabled={deletingId === doc.id}
                    className="text-xs text-ink-subtle hover:text-status-danger"
                  >
                    {deletingId === doc.id ? "…" : "Delete"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {isAdmin && (
        <KBUploadModal
          open={pickerOpen}
          onCancel={() => setPickerOpen(false)}
          onConfirm={startUpload}
        />
      )}
    </AppShell>
  );
}
