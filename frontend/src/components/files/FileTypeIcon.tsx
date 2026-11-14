import { File, FileArchive, FileChartColumn, FileImage, FileSpreadsheet, FileText } from "lucide-react";
import { cn } from "@/lib/cn";
import { fileCategory, fileExtension, fileTypeLabel, type FileDescriptor } from "@/lib/files";

const CATEGORY_STYLES = {
    image: "text-accent/80",
    pdf: "text-danger/80",
    word: "text-accent-hover",
    sheet: "text-success/80",
    presentation: "text-warning/80",
    archive: "text-muted",
    text: "text-secondary",
    other: "text-muted",
} as const;

const CATEGORY_ICONS = {
    image: FileImage,
    pdf: File,
    word: FileText,
    sheet: FileSpreadsheet,
    presentation: FileChartColumn,
    archive: FileArchive,
    text: FileText,
    other: File,
} as const;

export function FileTypeIcon({
    file,
    className,
}: {
    file: Pick<FileDescriptor, "name" | "mime">;
    className?: string;
}) {
    const category = fileCategory(file);
    const markdown = category === "text" && (fileExtension(file.name) === "md" || file.mime?.split(";", 1)[0]?.trim().toLowerCase() === "text/markdown");
    const customMark = category === "pdf" || category === "word" || markdown;
    const Icon = CATEGORY_ICONS[category];

    return (
        <span
            className={cn(
                "relative flex h-11 w-11 shrink-0 items-center justify-center rounded-lg",
                CATEGORY_STYLES[category],
                className,
            )}
            aria-hidden="true"
        >
            {customMark ? <svg
                width="28"
                height="28"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.65"
                strokeLinecap="round"
                strokeLinejoin="round"
            >
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
                <path d="M14 2v6h6" />
                {category === "pdf" ? <path d="M7 18c2-3 6-9 5-10s-1 5 2 7 5 2 4 0-8 0-11 3Z" />
                    : markdown ? <><path d="M6.5 17v-6l3 3 3-3v6" /><path d="M16.5 11v6m-2-2 2 2 2-2" /></>
                        : <path d="m6.5 11 2 7 3.5-5 3.5 5 2-7" />}
            </svg> : <Icon size={28} strokeWidth={1.65} />}
            <span className="absolute bottom-0.5 max-w-[38px] truncate rounded-sm bg-surface px-0.5 py-px font-mono text-[8px] font-semibold leading-none text-secondary">
                {fileTypeLabel(file)}
            </span>
        </span>
    );
}
