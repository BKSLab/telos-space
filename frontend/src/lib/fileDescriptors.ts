import { apiUrl, endpoints } from "./api";
import type { FileDescriptor } from "./files";
import type { ProjectFile, TaskAttachment } from "./types";
import { fileProcessingLabel } from "./projectFiles";

export function attachmentDescriptor(file: TaskAttachment): FileDescriptor {
    const base = endpoints.taskAttachment(file.task_id, file.id);
    return { key: String(file.id), name: file.original_name, url: apiUrl(`${base}/content`), size: file.size,
        mime: file.content_type, previewable: file.previewable, previewUrl: file.preview_url ? apiUrl(`${base}/preview`) : null,
        caption: file.caption, status: fileProcessingLabel(file),
        retryable: ["failed", "disabled", "empty"].includes(file.extraction_status ?? "") || file.fts_status === "failed" || file.index_status === "failed" };
}

export function projectFileDescriptor(file: ProjectFile): FileDescriptor {
    const base = endpoints.projectFile(file.project_id, file.id);
    return { key: String(file.id), name: file.original_name, url: apiUrl(`${base}/content`), size: file.size,
        mime: file.content_type, previewable: Boolean(file.previewable), previewUrl: file.preview_url ? apiUrl(`${base}/preview`) : null,
        caption: file.caption };
}
