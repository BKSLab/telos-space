import type { FileUploadDraft } from "@/lib/files";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { ErrorMessage } from "@/components/ui/States";
import { FileDraftList } from "./FileDraftList";

export function FileUploadDialog({ drafts, onChange, busy, error, onUpload, onClose }: {
    drafts: FileUploadDraft[];
    onChange: (drafts: FileUploadDraft[]) => void;
    busy: boolean;
    error?: string | null;
    onUpload: () => void;
    onClose: () => void;
}) {
    return <Modal title="Загрузить файлы" description="Добавьте необязательные подписи перед загрузкой." size="lg"
        isOpen={drafts.length > 0} isDismissable={!busy} onOpenChange={open => { if (!open && !busy) onClose(); }}
        footer={<>
            <Button disabled={busy} onClick={onClose}>Отмена</Button>
            <Button variant="primary" disabled={busy || drafts.length === 0} onClick={onUpload}>{busy ? "Загрузка…" : "Загрузить"}</Button>
        </>}>
        <div className="flex flex-col gap-3">
            {error && <ErrorMessage title="Не удалось загрузить файлы" message={error} />}
            <FileDraftList drafts={drafts} onChange={onChange} disabled={busy} />
        </div>
    </Modal>;
}
