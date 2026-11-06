import type { FileExtractionStatus, FileIndexStatus, ProjectFile } from "./types";

export const PROJECT_FILE_ACCEPT = [
    ".avif", ".bmp", ".csv", ".docx", ".gif", ".jpeg", ".jpg", ".log", ".md",
    ".pdf", ".png", ".txt", ".webp", ".xls", ".xlsm", ".xlsx",
].join(",");
export const MAX_PROJECT_FILE_BATCH = 10;
export const MAX_PROJECT_FILE_SIZE = 10 * 1024 * 1024;
export const PROJECT_FILE_PAGE_SIZE = 100;
export const PROJECT_FILE_TEXT_PAGE_SIZE = 8000;

export const EXTRACTION_LABELS: Record<FileExtractionStatus, string> = {
    pending: "Текст: в очереди",
    processing: "Извлекаем текст",
    ready: "Текст извлечён",
    empty: "Текст не найден",
    unsupported: "Извлечение недоступно",
    failed: "Ошибка извлечения",
};

export const INDEX_LABELS: Record<FileIndexStatus, string> = {
    pending: "Знания: в очереди",
    processing: "Добавляем в знания",
    ready: "В знаниях Ариса",
    failed: "Ошибка индексации",
    disabled: "В знания не добавлен",
};

export function isProjectFileProcessing(file: Pick<ProjectFile, "extraction_status" | "index_status">): boolean {
    // При выключенных знаниях worker не запускается, даже если текст ещё pending.
    if (file.index_status === "disabled") return false;
    return file.extraction_status === "pending" || file.extraction_status === "processing"
        || file.index_status === "pending" || file.index_status === "processing";
}

/** После ошибки чтения автоматические запросы прекращаются до явного повтора. */
export function projectFilesPollInterval(files: ProjectFile[] | undefined, failed: boolean): number | false {
    return !failed && files?.some(isProjectFileProcessing) ? 2000 : false;
}

export function projectFileId(value: string | null): number | null {
    if (!value || !/^[1-9]\d*$/.test(value)) return null;
    const id = Number(value);
    return Number.isSafeInteger(id) ? id : null;
}
