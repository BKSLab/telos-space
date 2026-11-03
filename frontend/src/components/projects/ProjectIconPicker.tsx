import { useEffect, useRef, useState } from "react";
import { ImageUp, Loader2, X } from "lucide-react";
import { api, endpoints } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
    findProjectIcon,
    iconImageProblem,
    lucideIconValue,
    PROJECT_ICONS,
    projectEmoji,
} from "@/lib/projectIcons";
import type { Project } from "@/lib/types";
import { useToast } from "@/lib/toast";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Popover } from "@/components/ui/Popover";
import { ProjectIcon } from "@/components/projects/ProjectIcon";

export const ICON_IMAGE_HINT = "PNG, JPG или WebP до 2 МБ";

interface ProjectIconPickerProps {
    /** Значение поля `icon`: `lucide:<имя>`, эмодзи или пустая строка. */
    value: string;
    color: string;
    onChange: (icon: string) => void;
    /** Сохранённый проект: только у него можно загрузить свою картинку. */
    project?: Pick<Project, "id" | "icon_image_version">;
    /** Картинка загружена или убрана — сервер вернул обновлённый проект. */
    onProjectChange?: (project: Project) => void;
    /**
     * Проекта ещё нет (форма создания): выбранная картинка держится на странице и
     * загружается сразу после создания. Без этого колбэка картинку выбрать нельзя.
     */
    pendingImage?: File | null;
    onPendingImageChange?: (file: File | null) => void;
    disabled?: boolean;
}

/**
 * Выбор иконки проекта: сетка иконок набора цветом проекта, эмодзи (старый
 * формат) и своя картинка. Иконка сохраняется вместе с формой, а картинка —
 * сразу при загрузке: у неё отдельный адрес и проверка на сервере.
 */
export function ProjectIconPicker({
    value,
    color,
    onChange,
    project,
    onProjectChange,
    pendingImage,
    onPendingImageChange,
    disabled = false,
}: ProjectIconPickerProps) {
    const toast = useToast();
    const fileInput = useRef<HTMLInputElement>(null);
    const [busy, setBusy] = useState(false);
    const selected = findProjectIcon(value);
    const emoji = projectEmoji(value) ?? "";
    // Предпросмотр выбранного до создания файла: адрес заводится при выборе и освобождается при смене,
    // удалении и уходе со страницы.
    const [preview, setPreview] = useState<{ file: File; url: string } | null>(null);
    const previewRef = useRef(preview);
    useEffect(() => {
        previewRef.current = preview;
    }, [preview]);
    useEffect(() => () => {
        if (previewRef.current) URL.revokeObjectURL(previewRef.current.url);
    }, []);
    const pendingUrl = !project && pendingImage && preview?.file === pendingImage ? preview.url : null;

    function choosePending(file: File | null) {
        if (preview) URL.revokeObjectURL(preview.url);
        setPreview(file ? { file, url: URL.createObjectURL(file) } : null);
        onPendingImageChange?.(file);
    }
    const hasImage = Boolean(project?.icon_image_version) || Boolean(pendingUrl);
    const canPickImage = Boolean(project) || Boolean(onPendingImageChange);

    async function uploadImage(file: File) {
        const problem = iconImageProblem(file);
        if (problem) {
            toast.error(`${problem} Нужна картинка: ${ICON_IMAGE_HINT}.`);
            return;
        }
        if (!project) {
            choosePending(file);
            return;
        }
        const body = new FormData();
        body.append("file", file);
        setBusy(true);
        try {
            const saved = await api.postForm<Project>(endpoints.projectIconImage(project.id), body);
            onProjectChange?.(saved);
            toast.success("Картинка проекта загружена");
        } catch (error) {
            toast.error((error as Error).message);
        } finally {
            setBusy(false);
        }
    }

    async function removeImage() {
        if (!project) {
            choosePending(null);
            return;
        }
        setBusy(true);
        try {
            const saved = await api.delete<Project>(endpoints.projectIconImage(project.id));
            onProjectChange?.(saved);
            toast.success("Картинка убрана — проект снова показывает иконку");
        } catch (error) {
            toast.error((error as Error).message);
        } finally {
            setBusy(false);
        }
    }

    // Как иконку покажет тема: бордовый #7C1623 на тёмном поповере иначе почти не виден (lib/dataColor.ts).
    const shownColor = color;
    return (
        <Popover
            label="Иконка проекта"
            align="start"
            width={332}
            disabled={disabled}
            triggerClassName="h-9 gap-2 px-2.5"
            trigger={
                <>
                    <span className="grid size-5 place-items-center" data-testid="icon-picker-preview">
                        {pendingUrl ? (
                            <img src={pendingUrl} alt="" aria-hidden="true" className="size-[18px] rounded-[4px] object-cover" data-testid="icon-picker-pending" />
                        ) : (
                            <ProjectIcon
                                project={{
                                    id: project?.id ?? 0,
                                    color,
                                    icon: value || null,
                                    icon_image_version: project?.icon_image_version,
                                }}
                                size={18}
                                dotClassName="size-2.5"
                            />
                        )}
                    </span>
                    <span className="text-[13px]">
                        {hasImage ? "Своя картинка" : selected?.label ?? (emoji ? "Эмодзи" : "Без иконки")}
                    </span>
                </>
            }
        >
            {({ close }) => (
                <div className="flex flex-col gap-3">
                    {hasImage && (
                        <p className="text-[12px] text-muted">
                            Сейчас показывается своя картинка. Выбранная иконка вернётся, если её убрать.
                        </p>
                    )}
                    <div
                        role="group"
                        aria-label="Иконки набора"
                        className="icon-picker-grid grid grid-cols-8 gap-1"
                    >
                        {PROJECT_ICONS.map(({ name, label, Icon }) => {
                            const isSelected = selected?.name === name;
                            return (
                                <button
                                    key={name}
                                    type="button"
                                    title={label}
                                    aria-label={label}
                                    aria-pressed={isSelected}
                                    onClick={() => {
                                        onChange(lucideIconValue(name));
                                        close();
                                    }}
                                    className={cn(
                                        "touch-target grid size-8 place-items-center rounded-[6px] transition-colors duration-[var(--duration-fast)]",
                                        "hover:bg-hover focus-visible:outline-2 focus-visible:outline-offset-1",
                                        isSelected && "ring-2 ring-offset-1 ring-offset-app",
                                    )}
                                    style={
                                        isSelected
                                            ? { backgroundColor: `color-mix(in srgb, ${shownColor} 14%, transparent)`, ["--tw-ring-color" as string]: shownColor }
                                            : undefined
                                    }
                                >
                                    <Icon size={16} strokeWidth={2} style={{ color: shownColor }} aria-hidden="true" />
                                </button>
                            );
                        })}
                    </div>

                    <div className="flex items-center gap-2">
                        <Input
                            aria-label="Эмодзи вместо иконки"
                            title="Можно вписать эмодзи вместо иконки из набора"
                            placeholder="или эмодзи 🚀"
                            maxLength={8}
                            value={emoji}
                            onChange={(event) => onChange(event.target.value)}
                            className="h-8 flex-1"
                        />
                        <Button
                            size="sm"
                            variant="ghost"
                            title="Убрать иконку: проект будет отмечен цветной точкой"
                            disabled={!value}
                            onClick={() => {
                                onChange("");
                                close();
                            }}
                        >
                            Без иконки
                        </Button>
                    </div>

                    <div className="flex flex-col gap-1.5 border-t border-line pt-3">
                        <span className="text-xs font-medium text-secondary">Своя картинка</span>
                        {canPickImage ? (
                            <>
                                <div className="flex flex-wrap gap-2">
                                    <input
                                        ref={fileInput}
                                        type="file"
                                        accept="image/png,image/jpeg,image/webp"
                                        className="sr-only"
                                        tabIndex={-1}
                                        aria-label="Файл картинки проекта"
                                        onChange={(event) => {
                                            const file = event.target.files?.[0];
                                            event.target.value = "";
                                            if (file) void uploadImage(file);
                                        }}
                                    />
                                    <Button
                                        size="sm"
                                        icon={busy ? <Loader2 size={14} className="animate-spin motion-reduce:animate-none" /> : <ImageUp size={14} />}
                                        title={
                                            project
                                                ? "Загрузить свою картинку: она заменит иконку в меню, списках и шапке проекта"
                                                : "Выбрать свою картинку: загрузится сразу после создания проекта и заменит иконку"
                                        }
                                        disabled={busy || disabled}
                                        onClick={() => fileInput.current?.click()}
                                    >
                                        {hasImage ? "Заменить картинку" : project ? "Загрузить картинку" : "Выбрать картинку"}
                                    </Button>
                                    {hasImage && (
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            icon={<X size={14} />}
                                            title="Убрать картинку: проект снова покажет иконку или цветную точку"
                                            disabled={busy || disabled}
                                            onClick={() => void removeImage()}
                                        >
                                            Убрать картинку
                                        </Button>
                                    )}
                                </div>
                                <span className="text-[12px] text-muted">
                                    {project ? `${ICON_IMAGE_HINT}. Сохраняется сразу.` : `${ICON_IMAGE_HINT}. Загрузится сразу после создания проекта.`}
                                </span>
                            </>
                        ) : (
                            <span className="text-[12px] text-muted">
                                Загрузить свою картинку ({ICON_IMAGE_HINT}) можно в настройках после создания проекта.
                            </span>
                        )}
                    </div>
                </div>
            )}
        </Popover>
    );
}
