export interface FileDescriptor {
    key: string;
    name: string;
    url: string | null;
    size: number | null;
    mime: string | null;
    previewable: boolean;
    previewUrl?: string | null;
    caption?: string | null;
    status?: string | null;
    retryable?: boolean;
}

export interface FileUploadDraft {
    file: File;
    caption: string;
}

export function fileDraftKey(file: File): string {
    return `${file.name}:${file.size}:${file.lastModified}`;
}

export function mergeFileDrafts(current: FileUploadDraft[], incoming: File[]): FileUploadDraft[] {
    const keys = new Set(current.map(({ file }) => fileDraftKey(file)));
    return [...current, ...incoming.filter(file => {
        const key = fileDraftKey(file);
        if (keys.has(key)) return false;
        keys.add(key);
        return true;
    }).map(file => ({ file, caption: "" }))];
}

export type FileCategory =
    | "image"
    | "pdf"
    | "word"
    | "sheet"
    | "presentation"
    | "archive"
    | "text"
    | "other";

const IMAGE_EXTENSIONS = new Set(["avif", "bmp", "gif", "jpeg", "jpg", "png", "webp"]);
const WORD_EXTENSIONS = new Set(["doc", "docx", "odt", "rtf"]);
const SHEET_EXTENSIONS = new Set(["csv", "ods", "xls", "xlsm", "xlsx"]);
const PRESENTATION_EXTENSIONS = new Set(["odp", "ppt", "pptx"]);
const ARCHIVE_EXTENSIONS = new Set(["7z", "gz", "rar", "tar", "zip"]);
const TEXT_EXTENSIONS = new Set(["log", "md", "txt"]);

export function fileExtension(name: string): string {
    const cleanName = name.split(/[?#]/, 1)[0] ?? name;
    const dotIndex = cleanName.lastIndexOf(".");
    if (dotIndex <= 0 || dotIndex === cleanName.length - 1) return "";
    return cleanName.slice(dotIndex + 1).toLocaleLowerCase("ru-RU");
}

export function fileCategory(file: Pick<FileDescriptor, "name" | "mime">): FileCategory {
    const extension = fileExtension(file.name);
    const mime = file.mime?.split(";", 1)[0]?.trim().toLowerCase() ?? "";

    if (IMAGE_EXTENSIONS.has(extension) || mime.startsWith("image/")) return "image";
    if (extension === "pdf" || mime === "application/pdf") return "pdf";
    if (
        WORD_EXTENSIONS.has(extension)
        || mime.includes("wordprocessingml")
        || mime === "application/msword"
    ) return "word";
    if (
        SHEET_EXTENSIONS.has(extension)
        || mime.includes("spreadsheetml")
        || mime === "application/vnd.ms-excel"
    ) return "sheet";
    if (
        PRESENTATION_EXTENSIONS.has(extension)
        || mime.includes("presentationml")
        || mime === "application/vnd.ms-powerpoint"
    ) return "presentation";
    if (ARCHIVE_EXTENSIONS.has(extension) || mime.includes("zip") || mime.includes("compressed")) {
        return "archive";
    }
    if (TEXT_EXTENSIONS.has(extension) || mime.startsWith("text/")) return "text";
    return "other";
}

export function fileTypeLabel(file: Pick<FileDescriptor, "name" | "mime">): string {
    const extension = fileExtension(file.name);
    if (extension) return extension.slice(0, 5).toUpperCase();

    const mimeSubtype = file.mime?.split("/", 2)[1]?.split(/[;+]/, 1)[0]?.trim();
    return mimeSubtype ? mimeSubtype.slice(0, 5).toUpperCase() : "FILE";
}

export function formatFileSize(size: number | null | undefined): string | null {
    if (size == null || !Number.isFinite(size) || size < 0) return null;
    if (size < 1024) return `${size} Б`;

    const units = ["КБ", "МБ", "ГБ", "ТБ"];
    let value = size / 1024;
    let unitIndex = 0;

    while (value >= 1024 && unitIndex < units.length - 1) {
        value /= 1024;
        unitIndex += 1;
    }

    return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value)} ${units[unitIndex]}`;
}
