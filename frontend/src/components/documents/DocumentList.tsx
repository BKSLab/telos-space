import { useState, type ReactNode } from "react";
import { formatFullDate } from "@/lib/dates";
import { documentProcessingLabel } from "@/lib/documents";
import { fileTypeLabel, formatFileSize } from "@/lib/files";
import type { DocumentListItem } from "@/lib/types";
import { FileTypeIcon } from "@/components/files/FileTypeIcon";
import { DocumentDialog } from "./DocumentView";

export function DocumentList({ documents, onOpen, actions, projectKey }: {
    documents: DocumentListItem[];
    onOpen?: (document: DocumentListItem) => void;
    actions?: (document: DocumentListItem) => ReactNode;
    projectKey?: string;
}) {
    const [opened, setOpened] = useState<string | null>(null);
    return <>
        <div className="min-w-0 divide-y divide-line-subtle overflow-hidden rounded-[var(--radius-card)] border border-line-subtle bg-surface/30">
            {documents.map(document => <DocumentRow key={document.ref} document={document} actions={actions?.(document)}
                onOpen={() => onOpen ? onOpen(document) : setOpened(document.ref)} />)}
        </div>
        {opened && <DocumentDialog key={opened} documentRef={opened} projectKey={projectKey} onClose={() => setOpened(null)} />}
    </>;
}

export function DocumentRow({ document, onOpen, actions }: { document: DocumentListItem; onOpen: () => void; actions?: ReactNode }) {
    const file = { name: document.original?.filename ?? "document.md", mime: document.original?.content_type ?? "text/markdown" };
    const status = documentProcessingLabel(document);
    const size = formatFileSize(document.original?.size);
    return <div className="flex min-w-0 items-center gap-1 pr-1">
        <button type="button" aria-label={`Открыть документ ${document.title}`} onClick={onOpen}
            className="flex min-h-14 min-w-0 flex-1 items-center gap-3 px-3 py-2 text-left hover:bg-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent">
            <FileTypeIcon file={file} className="h-9 w-9" />
            <span className="flex min-w-0 flex-1 flex-col gap-1">
                <span className="truncate text-[13px] font-medium text-primary" title={document.title}>{document.title}</span>
                {document.caption && <span className="line-clamp-1 break-words text-[12px] text-secondary">{document.caption}</span>}
                <span className="flex min-w-0 flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted">
                    <span>{fileTypeLabel(file)}{size ? ` · ${size}` : ""}</span>
                    <span className="max-w-40 truncate" title={document.tasks.map(task => `${task.key} · ${task.title}`).join("\n")}>{document.tasks.length > 0 ? `${document.tasks[0].key}${document.tasks.length > 1 ? ` · ещё ${document.tasks.length - 1}` : ""}` : "Проект"}</span>
                    {status && <span>{status}</span>}
                </span>
            </span>
            <time className="hidden shrink-0 text-[11px] text-muted lg:block" dateTime={document.updated_at}>{formatFullDate(document.updated_at)}</time>
        </button>
        {actions && <div className="shrink-0">{actions}</div>}
    </div>;
}
