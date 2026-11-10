import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiUrl } from "@/lib/api";
import { projectFileDescriptor } from "@/lib/fileDescriptors";
import { fileTypeLabel, formatFileSize } from "@/lib/files";
import { MAX_ORIGINAL_TEXT_PREVIEW_BYTES, ORIGINAL_TEXT_TOO_LARGE, originalTextFormat, readOriginalText } from "@/lib/projectFilePreview";
import type { ProjectFile } from "@/lib/types";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { Button } from "@/components/ui/Button";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { FileTypeIcon } from "./FileTypeIcon";

export function FileMarkdown({ text }: { text: string }) {
    const html = useRenderedMarkdown(text);
    return <div className="markdown-body min-w-0 [overflow-wrap:anywhere] [&_pre]:max-w-full [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto [&_table]:[overflow-wrap:normal]" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function ProjectFileOriginal({ file }: { file: ProjectFile }) {
    const [imageAttempt, setImageAttempt] = useState(0);
    const [imageFailed, setImageFailed] = useState(false);
    const [imageLoaded, setImageLoaded] = useState(false);
    const format = originalTextFormat(file);
    const tooLarge = file.size > MAX_ORIGINAL_TEXT_PREVIEW_BYTES;
    const text = useQuery({
        queryKey: ["projects", file.project_id, "files", "original", file.id],
        queryFn: ({ signal }) => readOriginalText(file.project_id, file.id, signal),
        enabled: format !== null && !tooLarge,
        retry: false, staleTime: Infinity, gcTime: 0,
    });

    if (file.previewable && file.preview_url) return (
        <section aria-label="Изображение" className="flex min-h-40 min-w-0 flex-col items-center justify-center gap-3 rounded-[var(--radius-card)] bg-app/50 p-3">
            {!imageFailed ? <>
                {!imageLoaded && <p role="status" className="text-[13px] text-muted">Загрузка изображения…</p>}
                <img key={imageAttempt} src={apiUrl(file.preview_url)} alt={file.original_name}
                    className="max-h-[60dvh] max-w-full rounded-[var(--radius-control)] object-contain"
                    onLoad={() => setImageLoaded(true)} onError={() => setImageFailed(true)} />
            </> : <ErrorMessage title="Не удалось загрузить изображение" message="Повторите загрузку или скачайте оригинал."
                action={<Button onClick={() => { setImageFailed(false); setImageLoaded(false); setImageAttempt(value => value + 1); }}>Загрузить изображение снова</Button>} />}
        </section>
    );

    if (format !== null && !tooLarge) return (
        <section aria-label="Содержимое оригинала" className="min-w-0 rounded-[var(--radius-card)] bg-app/40 p-3 sm:p-5">
            {text.isPending && <div role="status" aria-label="Загрузка исходного текста"><Skeleton className="h-40 w-full" /></div>}
            {text.error && <ErrorMessage title="Не удалось открыть оригинал" message={text.error.message}
                action={<Button disabled={text.isFetching} onClick={() => void text.refetch()}>Повторить загрузку оригинала</Button>} />}
            {text.data !== undefined && (format === "markdown" ? <FileMarkdown text={text.data} /> :
                <pre tabIndex={0} className="whitespace-pre-wrap font-sans text-[14px] leading-relaxed break-words text-primary [overflow-wrap:anywhere] focus-visible:outline-2 focus-visible:outline-accent">{text.data}</pre>)}
            {text.data === "" && <p className="text-[13px] text-muted">В исходном файле нет текста.</p>}
        </section>
    );

    const descriptor = projectFileDescriptor(file);
    return <div className="flex min-w-0 flex-col items-center gap-3 rounded-[var(--radius-card)] bg-app/40 px-4 py-8 text-center">
        <FileTypeIcon file={descriptor} className="h-14 w-14" />
        <p className="text-[13px] text-secondary">{tooLarge && format ? ORIGINAL_TEXT_TOO_LARGE : "Для этого формата встроенный просмотр недоступен. Скачайте оригинал кнопкой внизу."}</p>
        <p className="text-[12px] text-muted">{fileTypeLabel(descriptor)} · {formatFileSize(file.size)}</p>
    </div>;
}
