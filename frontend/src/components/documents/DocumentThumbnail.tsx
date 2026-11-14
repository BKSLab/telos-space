import { useEffect, useRef, useState } from "react";
import { apiUrl, endpoints } from "@/lib/api";
import { fileCategory, type FileDescriptor } from "@/lib/files";
import type { DocumentListItem } from "@/lib/types";
import { FileTypeIcon } from "@/components/files/FileTypeIcon";

type ThumbnailFile = Pick<FileDescriptor, "name" | "mime">;

export function DocumentThumbnail({ document, file }: { document: DocumentListItem; file: ThumbnailFile }) {
    const previewUrl = document.original?.preview_url;
    const canPreview = document.capabilities.can_read && fileCategory(file) === "image"
        && previewUrl === `${endpoints.document(document.ref)}/preview`;

    return canPreview && previewUrl
        ? <ImageThumbnail key={`${document.ref}:${document.revision}:${previewUrl}`} file={file} previewUrl={previewUrl} />
        : <FileTypeIcon file={file} className="h-9 w-9" />;
}

function ImageThumbnail({ file, previewUrl }: { file: ThumbnailFile; previewUrl: string }) {
    const element = useRef<HTMLSpanElement>(null);
    const objectUrl = useRef<string | null>(null);
    const [src, setSrc] = useState<string | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        let started = false;
        let observer: IntersectionObserver | undefined;

        async function load() {
            if (started || controller.signal.aborted) return;
            started = true;
            observer?.disconnect();
            try {
                const response = await fetch(apiUrl(previewUrl), { credentials: "include", signal: controller.signal, redirect: "error" });
                if (!response.ok) return;
                const blob = await response.blob();
                // The protected endpoint returns a server-rendered JPEG, never the original.
                if (controller.signal.aborted || blob.type.split(";", 1)[0] !== "image/jpeg") return;
                objectUrl.current = URL.createObjectURL(blob);
                setSrc(objectUrl.current);
            } catch {
                // Keep the format icon for denied, missing or interrupted previews.
            }
        }

        if (typeof IntersectionObserver === "undefined") {
            void load();
        } else {
            observer = new IntersectionObserver(entries => {
                if (entries.some(entry => entry.isIntersecting)) void load();
            }, { rootMargin: "120px" });
            if (element.current) observer.observe(element.current);
        }
        return () => {
            controller.abort();
            observer?.disconnect();
            if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
            objectUrl.current = null;
        };
    }, [previewUrl]);

    function fail() {
        if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
        objectUrl.current = null;
        setSrc(null);
    }

    return <span ref={element} aria-hidden="true" className="relative flex h-9 w-9 shrink-0 overflow-hidden rounded-lg">
        <FileTypeIcon file={file} className="h-9 w-9" />
        {src && <img src={src} alt="" loading="lazy" decoding="async" className="absolute inset-0 h-full w-full object-cover" onError={fail} />}
    </span>;
}
