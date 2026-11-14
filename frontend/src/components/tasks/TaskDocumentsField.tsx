import { useState, type Dispatch, type SetStateAction } from "react";
import { Trash2 } from "lucide-react";
import { fileDraftKey, type FileUploadDraft } from "@/lib/files";
import type { MarkdownDocumentDraft } from "@/lib/documents";
import type { DocumentListItem } from "@/lib/types";
import { DocumentAddButton, DocumentComposeDialog } from "@/components/documents/DocumentComposeDialog";
import { DocumentList } from "@/components/documents/DocumentList";
import { FileDraftList } from "@/components/files/FileDraftList";
import { FileTypeIcon } from "@/components/files/FileTypeIcon";
import { IconButton } from "@/components/ui/Button";

export function TaskDocumentsField({ projectId, documents, files, markdown, disabled, onDocumentsChange, onFilesChange, onMarkdownChange }: {
    projectId: number;
    documents: DocumentListItem[];
    files: FileUploadDraft[];
    markdown: MarkdownDocumentDraft[];
    disabled?: boolean;
    onDocumentsChange: (documents: DocumentListItem[]) => void;
    onFilesChange: Dispatch<SetStateAction<FileUploadDraft[]>>;
    onMarkdownChange: (markdown: MarkdownDocumentDraft[]) => void;
}) {
    const [adding, setAdding] = useState(false);
    return <div className="flex min-w-0 flex-col gap-3">
        <div><DocumentAddButton disabled={disabled} onClick={() => setAdding(true)} /></div>
        {documents.length > 0 && <DocumentList documents={documents} actions={document => <IconButton label={`Убрать документ ${document.title}`} disabled={disabled} onClick={() => onDocumentsChange(documents.filter(item => item.ref !== document.ref))}><Trash2 size={13} /></IconButton>} />}
        {markdown.length > 0 && <div className="divide-y divide-line-subtle rounded border border-line-subtle">{markdown.map(draft => <div key={draft.key} className="flex min-w-0 items-center gap-3 p-2.5"><FileTypeIcon file={{ name: "document.md", mime: "text/markdown" }} className="h-8 w-8" /><span className="min-w-0 flex-1"><span className="block truncate text-[13px] text-secondary">{draft.title}</span><span className="text-[10px] text-muted">Новый документ · Markdown</span></span><IconButton label={`Убрать документ ${draft.title}`} disabled={disabled} onClick={() => onMarkdownChange(markdown.filter(item => item.key !== draft.key))}><Trash2 size={13} /></IconButton></div>)}</div>}
        <FileDraftList drafts={files} disabled={disabled} onChange={onFilesChange} />
        {(documents.length > 0 || files.length > 0 || markdown.length > 0) && <p className="text-[11px] text-muted">Документы будут добавлены при создании задачи.</p>}
        <DocumentComposeDialog projectId={projectId} selectedRefs={documents.map(document => document.ref)} draftMode isOpen={adding} onClose={() => setAdding(false)}
            onMarkdown={draft => onMarkdownChange([...markdown, draft])}
            onUpload={draft => onFilesChange(current => [...current.filter(item => fileDraftKey(item.file) !== fileDraftKey(draft.file)), draft])}
            onExisting={selected => onDocumentsChange([...documents, ...selected.filter(document => !documents.some(item => item.ref === document.ref))])} />
    </div>;
}
