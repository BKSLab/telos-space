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
    pending: "Готовим текст",
    processing: "Извлекаем текст",
    ready: "Текст извлечён",
    empty: "Текст не найден",
    unsupported: "Извлечение недоступно",
    failed: "Ошибка извлечения",
    disabled: "Распознавание отключено",
};

export const INDEX_LABELS: Record<FileIndexStatus, string> = {
    pending: "Готовим знания Ариса",
    processing: "Добавляем в знания",
    ready: "В знаниях Ариса",
    failed: "Не удалось добавить в знания",
    disabled: "В знания не добавлен",
};

export interface FileProcessingState {
    extraction_status?: FileExtractionStatus;
    fts_status?: "pending" | "ready" | "failed";
    index_status?: FileIndexStatus;
}

export function isProjectFileProcessing(file: FileProcessingState, semantic = true): boolean {
    return file.extraction_status === "pending" || file.extraction_status === "processing"
        || file.fts_status === "pending"
        || (semantic && (file.index_status === "pending" || file.index_status === "processing"));
}

/** После ошибки чтения автоматические запросы прекращаются до явного повтора. */
export function projectFilesPollInterval(files: ProjectFile[] | undefined, failed: boolean): number | false {
    return !failed && files?.some(file => isProjectFileProcessing(file)) ? 2000 : false;
}

/** У вложения доступно состояние локального поиска; знания отслеживаются на уровне проекта. */
export function taskFilesPollInterval(files: FileProcessingState[] | undefined, failed: boolean): number | false {
    return !failed && files?.some(file => isProjectFileProcessing(file, false)) ? 2000 : false;
}

export function fileProcessingLabel(file: FileProcessingState): string | null {
    if (file.extraction_status === "pending" || file.extraction_status === "processing" || file.extraction_status === "failed") return EXTRACTION_LABELS[file.extraction_status];
    if (file.fts_status === "pending") return "Готовим для поиска";
    if (file.fts_status === "failed") return "Не удалось добавить в поиск";
    if (file.extraction_status === "disabled") return "Распознавание отключено; поиск по названию и подписи";
    if (file.extraction_status === "unsupported" || file.extraction_status === "empty") return "Поиск по названию и подписи";
    if (file.fts_status === "ready") return "Текст доступен для поиска";
    return null;
}

export function projectFileId(value: string | null): number | null {
    if (!value || !/^[1-9]\d*$/.test(value)) return null;
    const id = Number(value);
    return Number.isSafeInteger(id) ? id : null;
}
