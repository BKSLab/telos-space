import { useState } from "react";
import { Download, ExternalLink } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import type { FileDescriptor } from "@/lib/files";
import { FileTypeIcon } from "./FileTypeIcon";

export function FilePreviewModal({
    file,
    onClose,
}: {
    file: FileDescriptor | null;
    onClose: () => void;
}) {
    const [failedPreviewKey, setFailedPreviewKey] = useState<string | null>(null);
    const previewKey = `${file?.key ?? ""}:${file?.previewUrl ?? ""}`;
    const loadFailed = failedPreviewKey === previewKey;

    if (!file) {
        return null;
    }

    return (
        <Modal
            isOpen
            size="lg"
            title={file.name}
            onOpenChange={(isOpen) => {
                if (!isOpen) {
                    onClose();
                }
            }}
            footer={
                file.url ? (
                    <>
                        <a
                            href={file.url}
                            target="_blank"
                            rel="noreferrer"
                            className="touch-target material-metal inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-control)] border border-line-subtle px-3 text-[13px] font-medium text-primary hover:border-line hover:bg-hover"
                        >
                            Открыть оригинал
                            <ExternalLink size={13} aria-hidden="true" />
                        </a>
                        <a
                            href={file.url}
                            download={file.name}
                            className="touch-target inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-control)] border border-accent/45 bg-accent/85 px-3 text-[13px] font-semibold text-on-accent hover:bg-accent"
                        >
                            Скачать
                            <Download size={13} aria-hidden="true" />
                        </a>
                    </>
                ) : undefined
            }
        >
            {file.previewUrl && (
                <div className="flex min-h-52 items-center justify-center overflow-hidden rounded-[var(--radius-card)] bg-app/70">
                    {!loadFailed ? (
                        <img
                            src={file.previewUrl}
                            alt={file.name}
                            className="max-h-[62vh] max-w-full object-contain"
                            onError={() => setFailedPreviewKey(previewKey)}
                        />
                    ) : (
                        <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
                            <FileTypeIcon file={file} className="h-14 w-14" />
                            <p className="text-[13px] text-muted">
                                Не удалось загрузить изображение для просмотра.
                            </p>
                        </div>
                    )}
                </div>
            )}
        </Modal>
    );
}
