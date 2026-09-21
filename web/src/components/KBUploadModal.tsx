import { useEffect, useState } from "react";

import { api, type Group } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function KBUploadModal({
  open,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: (groupId: string | null) => void;
}) {
  const { user } = useAuth();
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [forGroup, setForGroup] = useState(false);
  const [groupId, setGroupId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || groups !== null) return;
    const orgId = user?.active_organization_id;
    if (!orgId) {
      setError("Couldn't load groups");
      return;
    }
    api
      .listOrgGroups(orgId)
      .then((rows) => {
        setGroups(rows);
        setGroupId(rows[0]?.id ?? "");
      })
      .catch(() => setError("Couldn't load groups"));
  }, [open, groups, user?.active_organization_id]);

  useEffect(() => {
    if (!open) {
      setForGroup(false);
      setError(null);
    }
  }, [open]);

  if (!open) return null;

  const canContinue = !forGroup || Boolean(groupId);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="card w-full max-w-sm p-6">
        <h2 className="font-serif text-lg text-ink dark:text-ink-inverted">
          Who is this document for?
        </h2>
        <p className="mt-1 text-sm text-ink-muted">
          Assign it to a group so that group&apos;s copilot can use it. Leave it
          unassigned if it shouldn&apos;t be shared.
        </p>

        {error && <p className="mt-4 text-sm text-status-danger">{error}</p>}

        <div className="mt-5 space-y-2">
          <label className="flex cursor-pointer items-center gap-2.5 rounded px-2 py-1.5 hover:bg-black/[0.03] dark:hover:bg-white/[0.04]">
            <input
              type="radio"
              name="kb-scope"
              checked={!forGroup}
              onChange={() => setForGroup(false)}
              className="accent-accent"
            />
            <span className="text-sm text-ink dark:text-ink-inverted">Not assigned to a group</span>
          </label>
          <label className="flex cursor-pointer items-center gap-2.5 rounded px-2 py-1.5 hover:bg-black/[0.03] dark:hover:bg-white/[0.04]">
            <input
              type="radio"
              name="kb-scope"
              checked={forGroup}
              onChange={() => setForGroup(true)}
              className="accent-accent"
            />
            <span className="text-sm text-ink dark:text-ink-inverted">A group</span>
          </label>
        </div>

        {forGroup && (
          <div className="mt-3">
            {groups === null && !error && <p className="text-sm text-ink-muted">Loading groups…</p>}
            {groups !== null && groups.length === 0 && (
              <p className="text-sm text-ink-muted">
                No groups yet. Create one under Admin first.
              </p>
            )}
            {groups !== null && groups.length > 0 && (
              <select
                className="field w-full"
                value={groupId}
                onChange={(e) => setGroupId(e.target.value)}
              >
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="btn-secondary">
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(forGroup ? groupId || null : null)}
            disabled={!canContinue || (forGroup && groups !== null && groups.length === 0)}
            className="btn-primary"
          >
            Choose file
          </button>
        </div>
      </div>
    </div>
  );
}
