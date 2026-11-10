import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiUrl, endpoints } from "@/lib/api";
import type { FileDescriptor } from "@/lib/files";
import type { FileSearchItem } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import { SearchHighlight } from "@/components/ui/SearchHighlight";
import { FilePreviewModal } from "./FilePreviewModal";
import { FileTile } from "./FileTile";

export function FileSearchResults({ items, projectKey, onOpenProjectFile }: {
    items: FileSearchItem[];
    projectKey?: string;
    onOpenProjectFile?: (id: number) => void;
}) {
    const navigate = useNavigate();
    const setSelectedTaskId = useUiStore(state => state.setSelectedTaskId);
    const [preview, setPreview] = useState<FileDescriptor | null>(null);
    return <>
        <ul className="flex min-w-0 flex-col gap-2" aria-label="Результаты поиска файлов">
            {items.map(item => {
                const url = item.source_type === "project_file" && item.project_id !== null
                    ? endpoints.projectFileContent(item.project_id, item.source_id)
                    : item.source_type === "task_attachment" && item.task_id !== null
                        ? `${endpoints.taskAttachment(item.task_id, item.source_id)}/content` : null;
                const descriptor: FileDescriptor = { key: `${item.source_type}:${item.source_id}`, name: item.title, url: url ? apiUrl(url) : null,
                    size: null, mime: item.content_type, caption: item.caption, previewable: Boolean(item.preview_url), previewUrl: item.preview_url && url ? apiUrl(url.replace(/\/content$/, "/preview")) : null };
                return <li key={descriptor.key}>
                    <FileTile file={descriptor} className="w-full" onPreview={setPreview}
                        onOpen={item.source_type === "project_file" && onOpenProjectFile ? () => onOpenProjectFile(item.source_id)
                            : item.document_slug && projectKey ? () => navigate(`/projects/${projectKey}/docs/${item.document_slug}`) : undefined}
                        openLabel={item.source_type === "project_file" && onOpenProjectFile ? `Открыть файл ${item.title}`
                            : item.source_type === "document" ? `Открыть документ ${item.title}` : undefined}>
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
                            <span>{item.source_type === "project_file" ? "Файл проекта" : item.source_type === "document" ? "Документ" : "Файл задачи"}</span>
                            {item.task_id !== null && <button type="button" className="touch-target max-w-full truncate rounded text-left text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent"
                                onClick={() => setSelectedTaskId(item.task_id!)} title={item.task_title ?? undefined}>
                                {item.task_key ?? `Задача №${item.task_id}`}{item.task_title ? ` · ${item.task_title}` : ""}
                            </button>}
                        </div>
                        {item.excerpt && <p className="line-clamp-3 break-words text-[12px] leading-relaxed text-secondary [overflow-wrap:anywhere]"><SearchHighlight text={item.excerpt} /></p>}
                    </FileTile>
                </li>;
            })}
        </ul>
        <FilePreviewModal file={preview} onClose={() => setPreview(null)} />
    </>;
}
