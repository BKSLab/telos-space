import { apiUrl, endpoints } from "@/lib/api";
import { fileCategory } from "@/lib/files";
import type { AgentFile } from "@/lib/types";
import { FileList } from "./FileList";

export function AgentFileList({ files, conversationId, projectId, onRemove, removeDisabled }: {
    files: AgentFile[];
    conversationId: number;
    projectId?: number;
    onRemove?: (id: string) => void;
    removeDisabled?: boolean;
}) {
    const base = projectId !== undefined ? endpoints.projectArisConversations(projectId) : endpoints.arisConversations();
    return <FileList files={files.map(file => ({
        key: file.id, name: file.original_name, size: file.size, mime: file.content_type,
        url: apiUrl(`${base}/${conversationId}/files/${file.id}/content`),
        previewUrl: apiUrl(`${base}/${conversationId}/files/${file.id}/preview`),
        previewable: fileCategory({ name: file.original_name, mime: file.content_type }) === "image",
    }))} onRemove={onRemove ? file => onRemove(file.key) : undefined} removeDisabled={removeDisabled} removeLabel={file => `Убрать ${file.name}`} className={onRemove ? "flex-nowrap [&>*]:shrink-0" : undefined} />;
}
