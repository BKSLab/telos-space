import { useState } from "react";
import type { FileDescriptor } from "@/lib/files";
import { cn } from "@/lib/cn";
import { FilePreviewModal } from "./FilePreviewModal";
import { FileTile } from "./FileTile";

export function FileList({
    files,
    onRemove,
    removeDisabled = false,
    onEditCaption,
    removeLabel,
    onRetry,
    retryDisabled = false,
    className,
}: {
    files: FileDescriptor[];
    onRemove?: (file: FileDescriptor) => void;
    removeDisabled?: boolean;
    onEditCaption?: (file: FileDescriptor) => void;
    removeLabel?: (file: FileDescriptor) => string;
    onRetry?: (file: FileDescriptor) => void;
    retryDisabled?: boolean;
    className?: string;
}) {
    const [previewFile, setPreviewFile] = useState<FileDescriptor | null>(null);

    if (files.length === 0) return null;

    return (
        <>
            <div className={cn("flex min-w-0 flex-wrap gap-2", className)}>
                {files.map((file) => (
                    <FileTile
                        key={file.key}
                        file={file}
                        onPreview={setPreviewFile}
                        onRemove={onRemove ? () => onRemove(file) : undefined}
                        removeDisabled={removeDisabled}
                        removeLabel={removeLabel?.(file)}
                    >
                        {onEditCaption && <button type="button" disabled={removeDisabled} onClick={() => onEditCaption(file)}
                            className="touch-target self-start rounded text-[11px] text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50">
                            {file.caption ? "Изменить подпись" : "Добавить подпись"}
                        </button>}
                        {onRetry && file.retryable && <button type="button" disabled={retryDisabled} aria-label={`Повторить обработку ${file.name}`} onClick={() => onRetry(file)}
                            className="touch-target self-start rounded text-[11px] text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50">Повторить обработку</button>}
                    </FileTile>
                ))}
            </div>
            <FilePreviewModal file={previewFile} onClose={() => setPreviewFile(null)} />
        </>
    );
}
