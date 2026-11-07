import { apiUrl } from "@/lib/api";
import { fileCategory } from "@/lib/files";
import { chatPath, type ChatAttachment as Attachment } from "@/lib/projectChat";
import { FileList } from "@/components/files/FileList";

export function ChatAttachment({ file, projectId, onRemove, removeDisabled }: { file: Attachment; projectId: number; onRemove?: () => void; removeDisabled?: boolean }) {
    const base = `${chatPath(projectId)}/attachments/${file.id}`;
    return <FileList files={[{
        key: file.id, name: file.original_name, url: apiUrl(`${base}/content`), size: file.size_bytes,
        mime: file.content_type, previewable: fileCategory({ name: file.original_name, mime: file.content_type }) === "image",
        previewUrl: apiUrl(`${base}/preview`),
    }]} onRemove={onRemove} removeDisabled={removeDisabled} removeLabel={file => `Убрать файл ${file.name}`} />;
}
