import { FileText, Library, Paperclip, X } from "lucide-react";
import { formatFileSize } from "@/lib/files";
import { MAX_PROJECT_FILE_BATCH, MAX_PROJECT_FILE_SIZE, PROJECT_FILE_ACCEPT } from "@/lib/projectFiles";
import type { DocumentListItem } from "@/lib/types";
import { IconButton } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";
import {
    FileUploadControl,
} from "@/components/files/FileUploadControl";

interface TaskDocumentsFieldProps {
    documents: DocumentListItem[];
    selectedDocumentIds: number[];
    files: File[];
    disabled?: boolean;
    loading?: boolean;
    error?: string | null;
    onSelectedDocumentIdsChange: (ids: number[]) => void;
    onFilesChange: (files: File[]) => void;
    onError: (message: string | null) => void;
}

export function TaskDocumentsField({
    documents,
    selectedDocumentIds,
    files,
    disabled = false,
    loading = false,
    error,
    onSelectedDocumentIdsChange,
    onFilesChange,
    onError,
}: TaskDocumentsFieldProps) {
    const selected = new Set(selectedDocumentIds);

    return (
        <div className="grid gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-medium text-secondary">Из проекта</span>
                <Popover
                    label="Документы проекта"
                    align="start"
                    width={360}
                    disabled={disabled}
                    triggerClassName="h-8 w-full justify-between px-3 font-normal"
                    trigger={
                        <>
                            <span className="inline-flex min-w-0 items-center gap-2">
                                <Library size={14} className="shrink-0 text-muted" aria-hidden="true" />
                                <span className="truncate">
                                    {selectedDocumentIds.length > 0
                                        ? `Выбрано: ${selectedDocumentIds.length}`
                                        : "Выбрать документы"}
                                </span>
                            </span>
                            <span className="text-[10px] text-muted">{documents.length}</span>
                        </>
                    }
                >
                    <div className="scrollbar-thin flex max-h-72 flex-col overflow-y-auto">
                        <p className="px-2 pb-2 text-[10px] font-semibold tracking-[0.12em] text-muted uppercase">
                            Связать с задачей
                        </p>
                        {loading && <p className="px-2 py-3 text-[12px] text-muted">Загрузка…</p>}
                        {!loading && documents.length === 0 && (
                            <p className="px-2 py-3 text-[12px] text-muted">
                                В проекте пока нет документов.
                            </p>
                        )}
                        {documents.map((document) => {
                            const checked = selected.has(document.id);
                            return (
                                <label
                                    key={document.id}
                                    className="flex cursor-pointer items-center gap-2.5 rounded-[var(--radius-control)] px-2 py-2 text-[13px] text-secondary hover:bg-white/[0.035]"
                                >
                                    <input
                                        type="checkbox"
                                        checked={checked}
                                        disabled={disabled}
                                        className="size-3.5 accent-accent"
                                        onChange={() =>
                                            onSelectedDocumentIdsChange(
                                                checked
                                                    ? selectedDocumentIds.filter(
                                                          (id) => id !== document.id,
                                                      )
                                                    : [...selectedDocumentIds, document.id],
                                            )
                                        }
                                    />
                                    <FileText size={14} className="shrink-0 text-muted" aria-hidden="true" />
                                    <span className="min-w-0 truncate">{document.title}</span>
                                </label>
                            );
                        })}
                    </div>
                </Popover>
                <span className="text-[10px] text-muted">Связь создастся вместе с задачей</span>
            </div>

            <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-medium text-secondary">Новые файлы</span>
                <div className="flex min-h-8 items-center">
                    <FileUploadControl
                        accept={PROJECT_FILE_ACCEPT}
                        maxFiles={MAX_PROJECT_FILE_BATCH}
                        maxFileSize={MAX_PROJECT_FILE_SIZE}
                        label={files.length > 0 ? `Выбрано файлов: ${files.length}` : "Загрузить документы"}
                        currentCount={files.length}
                        disabled={disabled}
                        onError={onError}
                        onFiles={(incoming) =>
                            onFilesChange(mergeUniqueFiles(files, incoming))
                        }
                    />
                </div>
                <span className="text-[10px] text-muted">Исходник и извлечённый текст сохранятся в проекте</span>
            </div>

            {files.length > 0 && (
                <div className="flex flex-col gap-1.5 sm:col-span-2">
                    {files.map((file) => (
                        <div
                            key={fileKey(file)}
                            className="flex items-center gap-2 rounded-[var(--radius-control)] bg-white/[0.025] px-2.5 py-1.5"
                        >
                            <Paperclip size={13} className="shrink-0 text-muted" aria-hidden="true" />
                            <span className="min-w-0 flex-1 truncate text-[12px] text-secondary">
                                {file.name}
                            </span>
                            <span className="shrink-0 text-[10px] text-disabled">
                                {formatFileSize(file.size)}
                            </span>
                            <IconButton
                                label={`Убрать файл ${file.name}`}
                                size="sm"
                                disabled={disabled}
                                onClick={() =>
                                    onFilesChange(files.filter((item) => fileKey(item) !== fileKey(file)))
                                }
                            >
                                <X size={12} aria-hidden="true" />
                            </IconButton>
                        </div>
                    ))}
                </div>
            )}

            {error && (
                <p role="alert" className="text-[11px] text-danger sm:col-span-2">
                    {error}
                </p>
            )}
        </div>
    );
}

function mergeUniqueFiles(current: File[], incoming: File[]): File[] {
    const result = [...current];
    const keys = new Set(current.map(fileKey));
    for (const file of incoming) {
        const key = fileKey(file);
        if (!keys.has(key)) {
            keys.add(key);
            result.push(file);
        }
    }
    return result;
}

function fileKey(file: File): string {
    return `${file.name}:${file.size}:${file.lastModified}`;
}
