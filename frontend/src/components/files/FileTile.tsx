import { useState } from "react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { formatFileSize, type FileDescriptor } from "@/lib/files";
import { FileTypeIcon } from "./FileTypeIcon";

export function FileTile({
    file,
    onPreview,
    onRemove,
    removeDisabled = false,
    onOpen,
    openLabel,
    className,
    children,
    removeLabel,
}: {
    file: FileDescriptor;
    onPreview?: (file: FileDescriptor) => void;
    onRemove?: () => void;
    removeDisabled?: boolean;
    onOpen?: () => void;
    openLabel?: string;
    className?: string;
    children?: ReactNode;
    removeLabel?: string;
}) {
    const [failedPreviewKey, setFailedPreviewKey] = useState<string | null>(null);
    const previewKey = `${file.key}:${file.previewUrl ?? ""}`;
    const canPreview = Boolean(
        file.previewable && file.previewUrl && failedPreviewKey !== previewKey,
    );
    const formattedSize = formatFileSize(file.size);
    const contentClassName = cn(
        "group flex min-w-0 flex-1 items-center gap-2.5 rounded-[var(--radius-control)] text-left",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        file.url && "hover:text-accent-hover",
    );

    const content = (
        <>
            {canPreview ? (
                <span className="relative h-11 w-11 shrink-0 overflow-hidden rounded-[var(--radius-control)] border border-line-subtle bg-surface">
                    <img
                        src={file.previewUrl!}
                        alt=""
                        loading="lazy"
                        className="h-full w-full object-cover"
                        onError={() => setFailedPreviewKey(previewKey)}
                    />
                    <span className="absolute inset-0 flex items-center justify-center bg-black/45 text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <circle cx="11" cy="11" r="7" />
                            <path d="m20 20-3.5-3.5M11 8v6M8 11h6" />
                        </svg>
                    </span>
                </span>
            ) : (
                <FileTypeIcon file={file} />
            )}
            <span className="min-w-0 flex-1">
                <span
                    className="block truncate text-xs font-semibold text-primary"
                    title={file.name}
                >
                    {file.name}
                </span>
                {formattedSize && (
                    <span className="mt-0.5 block text-[10px] font-medium text-muted">
                        {formattedSize}
                    </span>
                )}
                {file.caption && <span className="mt-1 line-clamp-2 block break-words text-[12px] font-normal text-secondary [overflow-wrap:anywhere]">{file.caption}</span>}
                {file.status && <span className="mt-1 block break-words text-[10px] font-normal text-muted">{file.status}</span>}
            </span>
        </>
    );

    return (
        <div className={cn("relative flex w-[240px] max-w-full flex-col gap-2 rounded-[var(--radius-card)] border border-line-subtle bg-surface-2 p-2 shadow-card transition-colors hover:border-line-strong", className)}>
            {onOpen || (canPreview && onPreview) ? (
                <button
                    type="button"
                    onClick={() => onOpen ? onOpen() : onPreview?.(file)}
                    className={cn(contentClassName, onRemove && "pr-11 sm:pr-6")}
                    aria-label={openLabel ?? `Открыть изображение ${file.name}`}
                >
                    {content}
                </button>
            ) : file.url ? (
                <a
                    href={file.url}
                    target="_blank"
                    rel="noreferrer"
                    className={cn(contentClassName, onRemove && "pr-11 sm:pr-6")}
                    aria-label={`Скачать файл ${file.name}`}
                >
                    {content}
                </a>
            ) : (
                <div className={cn(contentClassName, onRemove && "pr-11 sm:pr-6")}>{content}</div>
            )}
            {children}
            {onRemove && (
                <button
                    type="button"
                    onClick={onRemove}
                    disabled={removeDisabled}
                    className="touch-target absolute right-1.5 top-1.5 flex h-6 w-6 items-center justify-center rounded-md text-muted transition-colors hover:bg-danger/10 hover:text-danger focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-40"
                    aria-label={removeLabel ?? `Удалить файл ${file.name}`}
                    title="Удалить файл"
                >
                    <svg
                        width="12"
                        height="12"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                    >
                        <path d="m18 6-12 12M6 6l12 12" />
                    </svg>
                </button>
            )}
        </div>
    );
}
