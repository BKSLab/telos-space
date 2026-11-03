import { useState } from "react";
import { Download, FileText } from "lucide-react";
import { apiUrl } from "@/lib/api";
import { chatPath, type ChatAttachment as Attachment } from "@/lib/projectChat";
import { Modal } from "@/components/ui/Modal";

const sizeLabel = (size: number) => size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} КБ` : `${(size / 1024 / 1024).toFixed(1)} МБ`;

export function ChatAttachment({ file, projectId }: { file: Attachment; projectId: number }) {
    const [preview, setPreview] = useState(false);
    const url = apiUrl(`${chatPath(projectId)}/attachments/${file.id}/content`);
    const isImage = ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(file.content_type);
    return <div className="w-full min-w-0 max-w-sm">
        {isImage && <button type="button" aria-label={`Посмотреть изображение ${file.original_name}`} onClick={() => setPreview(true)} className="mb-1.5 block max-w-full overflow-hidden rounded-md bg-black/15 focus-visible:outline-2 focus-visible:outline-accent">
            <img src={`${url}?inline=true`} width={260} height={160} loading="lazy" alt={file.original_name} className="h-40 max-w-full object-contain" />
        </button>}
        <a href={url} className="flex min-h-11 items-center gap-2 rounded px-2 py-1.5 text-xs text-secondary hover:bg-hover hover:text-primary focus-visible:outline-2 focus-visible:outline-accent" download={file.original_name} title={file.original_name}>
            <FileText size={14} className="shrink-0 text-muted" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate">{file.original_name}</span>
            <span className="shrink-0 text-[10px] text-muted">{sizeLabel(file.size_bytes)}</span>
            <Download size={13} aria-hidden="true" />
        </a>
        {preview && <Modal title={file.original_name} isOpen onOpenChange={setPreview} size="lg">
            <div className="p-4"><img src={`${url}?inline=true`} alt={file.original_name} className="mx-auto max-h-[65vh] max-w-full object-contain" /></div>
        </Modal>}
    </div>;
}
