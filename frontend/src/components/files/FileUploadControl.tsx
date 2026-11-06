import {
    useCallback,
    useEffect,
    useRef,
    useState,
    type RefObject,
} from "react";
import { cn } from "@/lib/cn";

const MAX_TASK_FILES = 20;
const MAX_TASK_FILE_SIZE = 10 * 1024 * 1024;
const TASK_FILE_ACCEPT = [
    ".7z", ".avif", ".bmp", ".csv", ".doc", ".docx", ".gif", ".gz",
    ".jpeg", ".jpg", ".log", ".md", ".odp", ".ods", ".odt", ".pdf",
    ".png", ".ppt", ".pptx", ".rar", ".rtf", ".tar", ".txt", ".webp",
    ".xls", ".xlsx", ".zip",
].join(",");

function acceptsFile(file: File, accept: string): boolean {
    const name = file.name.toLowerCase();
    return accept.split(",").some((extension) => name.endsWith(extension));
}

function clipboardFiles(event: ClipboardEvent): File[] {
    const direct = Array.from(event.clipboardData?.files ?? []);
    const files = direct.length > 0
        ? direct
        : Array.from(event.clipboardData?.items ?? [])
            .filter((item) => item.kind === "file")
            .map((item) => item.getAsFile())
            .filter((file): file is File => Boolean(file));

    return files.map((file, index) => {
        if (file.name) return file;
        const extension = file.type.split("/", 2)[1] || "png";
        return new File([file], `clipboard-${Date.now()}-${index + 1}.${extension}`, {
            type: file.type,
        });
    });
}

export function FileUploadControl({
    onFiles,
    onError,
    currentCount,
    disabled = false,
    uploading = false,
    scopeRef,
    accept = TASK_FILE_ACCEPT,
    maxFiles = MAX_TASK_FILES,
    maxFileSize = MAX_TASK_FILE_SIZE,
    label = "Прикрепить файлы",
}: {
    onFiles: (files: File[]) => void | Promise<void>;
    onError: (message: string | null) => void;
    currentCount: number;
    disabled?: boolean;
    uploading?: boolean;
    scopeRef?: RefObject<HTMLElement | null>;
    accept?: string;
    maxFiles?: number;
    maxFileSize?: number;
    label?: string;
}) {
    const rootRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const [isDragging, setIsDragging] = useState(false);
    const blocked = disabled || uploading || currentCount >= maxFiles;

    const processFiles = useCallback((incoming: File[]) => {
        if (incoming.length === 0 || blocked) return;
        if (currentCount + incoming.length > maxFiles) {
            onError(`Можно выбрать не более ${maxFiles} файлов.`);
            return;
        }
        const oversized = incoming.find((file) => file.size > maxFileSize);
        if (oversized) {
            onError(
                `Файл «${oversized.name}» превышает ${Math.floor(maxFileSize / 1024 / 1024)} МБ.`,
            );
            return;
        }
        const empty = incoming.find((file) => file.size === 0);
        if (empty) {
            onError(`Файл «${empty.name}» пуст.`);
            return;
        }
        const unsupported = incoming.find((file) => !acceptsFile(file, accept));
        if (unsupported) {
            onError(`Тип файла «${unsupported.name}» не поддерживается.`);
            return;
        }
        onError(null);
        void onFiles(incoming);
    }, [accept, blocked, currentCount, maxFileSize, maxFiles, onError, onFiles]);

    useEffect(() => {
        const target = scopeRef?.current ?? rootRef.current;
        if (!target) return;
        const eventTarget = target;

        function handlePaste(event: ClipboardEvent) {
            const files = clipboardFiles(event);
            if (files.length === 0) return;
            event.preventDefault();
            processFiles(files);
        }

        function handleDragOver(event: DragEvent) {
            if (!event.dataTransfer?.types.includes("Files") || blocked) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "copy";
            setIsDragging(true);
        }

        function handleDragLeave(event: DragEvent) {
            const related = event.relatedTarget;
            if (related instanceof Node && eventTarget.contains(related)) return;
            setIsDragging(false);
        }

        function handleDrop(event: DragEvent) {
            if (!event.dataTransfer?.types.includes("Files") || blocked) return;
            event.preventDefault();
            setIsDragging(false);
            processFiles(Array.from(event.dataTransfer.files));
        }

        eventTarget.addEventListener("paste", handlePaste);
        eventTarget.addEventListener("dragover", handleDragOver);
        eventTarget.addEventListener("dragleave", handleDragLeave);
        eventTarget.addEventListener("drop", handleDrop);
        return () => {
            eventTarget.removeEventListener("paste", handlePaste);
            eventTarget.removeEventListener("dragover", handleDragOver);
            eventTarget.removeEventListener("dragleave", handleDragLeave);
            eventTarget.removeEventListener("drop", handleDrop);
        };
    }, [blocked, processFiles, scopeRef]);

    return (
        <div ref={rootRef}>
            <input
                ref={inputRef}
                type="file"
                aria-label={label}
                multiple
                accept={accept}
                className="hidden"
                disabled={blocked}
                onChange={(event) => {
                    processFiles(Array.from(event.currentTarget.files ?? []));
                    event.currentTarget.value = "";
                }}
            />
            <button
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={blocked}
                className={cn(
                    "touch-target inline-flex items-center gap-1.5 rounded-[var(--radius-control)] border border-dashed border-line-strong bg-white/[0.025] px-3 py-1.5 text-xs font-semibold text-primary transition-colors",
                    "hover:border-accent/50 hover:text-accent-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                    isDragging && "border-accent bg-accent/10 text-accent-hover",
                    blocked && "cursor-not-allowed opacity-50",
                )}
            >
                <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                >
                    <path d="m21.4 11.6-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5" />
                </svg>
                {uploading ? "Загрузка…" : label}
                <span className="font-normal text-muted">· Ctrl+V</span>
            </button>
        </div>
    );
}
