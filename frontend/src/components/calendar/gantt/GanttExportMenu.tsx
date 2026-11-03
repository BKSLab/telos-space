import { useState } from "react";
import { Download, FileImage, FileText, Presentation } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";

export type GanttExportScope = "project" | "screen";
export type GanttExportFormat = "png" | "pdf";

/** «Скачать»: Гант картинкой PNG или файлом PDF — весь срок проекта или период как на экране; и вход в «Режим показа». */
export function GanttExportMenu({
    busy,
    onExport,
    onPresent,
}: {
    busy: boolean;
    onExport: (format: GanttExportFormat, scope: GanttExportScope) => void;
    onPresent: () => void;
}) {
    const [scope, setScope] = useState<GanttExportScope>("project");
    return (
        <Popover
            label="Скачать Гант"
            width={260}
            trigger={
                <>
                    <Download size={13} aria-hidden="true" />
                    <span>{busy ? "Готовлю…" : "Скачать"}</span>
                </>
            }
            disabled={busy}
        >
            {({ close }) => (
                <div className="flex flex-col gap-2">
                    <p className="text-[11px] font-semibold tracking-[0.08em] text-muted uppercase">Что выгрузить</p>
                    <label className="flex min-h-11 items-center gap-2 text-[13px] text-secondary lg:min-h-0 lg:text-[12px]" title="От самого раннего начала до самого позднего срока задач, вех и проекта">
                        <input type="radio" name="gantt-export-scope" checked={scope === "project"} onChange={() => setScope("project")} />
                        Весь срок проекта
                    </label>
                    <label className="flex min-h-11 items-center gap-2 text-[13px] text-secondary lg:min-h-0 lg:text-[12px]" title="Тот же период и масштаб, что сейчас на шкале, но целиком, без прокрутки">
                        <input type="radio" name="gantt-export-scope" checked={scope === "screen"} onChange={() => setScope("screen")} />
                        Период как на шкале
                    </label>
                    <div className="mt-1 flex gap-2">
                        <Button size="sm" icon={<FileImage size={13} aria-hidden="true" />} title="Скачать картинку PNG — удобно вставить в письмо или чат" onClick={() => { close(); onExport("png", scope); }}>
                            PNG
                        </Button>
                        <Button size="sm" icon={<FileText size={13} aria-hidden="true" />} title="Скачать PDF — одна страница под размер шкалы, для печати и рассылки" onClick={() => { close(); onExport("pdf", scope); }}>
                            PDF
                        </Button>
                    </div>
                    <Button
                        size="sm"
                        variant="ghost"
                        icon={<Presentation size={13} aria-hidden="true" />}
                        title="Весь срок проекта на весь экран, без кнопок и ручек — для показа на встрече. Выход — Esc"
                        onClick={() => { close(); onPresent(); }}
                    >
                        Режим показа
                    </Button>
                </div>
            )}
        </Popover>
    );
}
