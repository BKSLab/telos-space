import { Loader2, X } from "lucide-react";
import type { PendingDraftUpload } from "@/lib/useDraftUploads";
import { Button, IconButton } from "@/components/ui/Button";

export function DraftUploadList({ entries, onCancel, onRetry }: {
    entries: PendingDraftUpload[];
    onCancel: (id: string) => void;
    onRetry: (id: string) => void;
}) {
    if (!entries.length) return null;
    return <ul aria-label="Загрузка файлов черновика" className="mb-2 flex min-w-0 flex-col gap-1">
        {entries.map(entry => <li key={entry.id} className="flex min-w-0 items-center gap-2 rounded-md border border-line-subtle bg-surface px-2 py-1 text-xs">
            {entry.status === "uploading" && <Loader2 size={13} className="shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />}
            <span className="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">
                <span className="text-secondary">{entry.name}</span>
                <span role={entry.status === "failed" ? "alert" : "status"} className={entry.status === "failed" ? "ml-2 text-danger" : "ml-2 text-muted"}>
                    {entry.status === "uploading" ? "Загрузка…" : entry.error}</span>
            </span>
            {entry.status === "failed" && <Button size="sm" onClick={() => onRetry(entry.id)}>Повторить</Button>}
            <IconButton label={`Отменить загрузку ${entry.name}`} onClick={() => onCancel(entry.id)}><X size={13} /></IconButton>
        </li>)}
    </ul>;
}
