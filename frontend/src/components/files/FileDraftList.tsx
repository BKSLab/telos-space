import { useEffect, useState } from "react";
import { fileCategory, fileDraftKey, type FileUploadDraft } from "@/lib/files";
import { Field, Input } from "@/components/ui/Field";
import { FileList } from "./FileList";

/** Локальный черновик: подпись отправляется только вместе с выбранным файлом. */
export function FileDraftList({ drafts, onChange, disabled = false }: {
    drafts: FileUploadDraft[];
    onChange: (drafts: FileUploadDraft[]) => void;
    disabled?: boolean;
}) {
    return <div className="flex min-w-0 flex-col gap-3">
        {drafts.map((draft, index) => <DraftFile key={fileDraftKey(draft.file)} draft={draft} disabled={disabled}
            onRemove={() => onChange(drafts.filter((_, i) => i !== index))}
            onCaption={caption => onChange(drafts.map((item, i) => i === index ? { ...item, caption } : item))} />)}
    </div>;
}

function DraftFile({ draft, disabled, onRemove, onCaption }: {
    draft: FileUploadDraft;
    disabled: boolean;
    onRemove: () => void;
    onCaption: (caption: string) => void;
}) {
    const { file, caption } = draft;
    const [url] = useState(() => typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : null);
    useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
    return <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start">
        <FileList files={[{ key: fileDraftKey(file), name: file.name, url, size: file.size, mime: file.type,
            previewable: fileCategory({ name: file.name, mime: file.type }) === "image", previewUrl: url }]}
            onRemove={onRemove} removeDisabled={disabled} />
        <Field label={`Подпись к ${file.name}`} className="min-w-0 flex-1" hint="Необязательно">
            {id => <Input id={id} value={caption} maxLength={2000} disabled={disabled} placeholder="Что важно в этом файле"
                onChange={event => onCaption(event.target.value)} />}
        </Field>
    </div>;
}
