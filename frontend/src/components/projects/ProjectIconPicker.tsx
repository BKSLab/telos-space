import { useEffect, useRef, useState } from "react";
import { Check, ImageUp, Loader2, Palette, X } from "lucide-react";
import { api, endpoints } from "@/lib/api";
import { cn } from "@/lib/cn";
import { findProjectIcon, iconImageProblem, lucideIconValue, PROJECT_PICKER_ICONS, projectEmoji } from "@/lib/projectIcons";
import { PROJECT_COLORS, type Project } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { ProjectIcon } from "@/components/projects/ProjectIcon";

export const ICON_IMAGE_HINT = "PNG, JPG или WebP до 2 МБ";

interface Appearance { icon: string; color: string }
interface ProjectIconPickerProps {
    value: string;
    color: string;
    onChange: (appearance: Appearance) => void;
    project?: Pick<Project, "id" | "icon_image_version">;
    onProjectChange?: (project: Project) => void;
    pendingImage?: File | null;
    onPendingImageChange?: (file: File | null) => void;
    disabled?: boolean;
}

/** Object URL принадлежит конкретному смонтированному preview и освобождается вместе с ним. */
function PendingImage({ file, className }: { file: File; className: string }) {
    const image = useRef<HTMLImageElement>(null);
    useEffect(() => {
        const target = image.current;
        const url = URL.createObjectURL(file);
        if (target) target.src = url;
        return () => {
            URL.revokeObjectURL(url);
            if (target?.getAttribute("src") === url) target.removeAttribute("src");
        };
    }, [file]);
    return <img ref={image} alt="" aria-hidden="true" className={className} data-testid="icon-picker-pending" />;
}

export function ProjectIconPicker(props: ProjectIconPickerProps) {
    const [open, setOpen] = useState(false);
    const selected = findProjectIcon(props.value);
    const hasImage = Boolean(props.project?.icon_image_version || props.pendingImage);
    return <>
        <Button onClick={() => setOpen(true)} disabled={props.disabled} aria-haspopup="dialog" aria-expanded={open}
            aria-label="Оформление проекта" className="h-auto min-h-11 max-w-full justify-start gap-3 px-3 py-2">
            <span className="grid size-8 shrink-0 place-items-center rounded-[6px] bg-surface/70" data-testid="icon-picker-preview">
                {props.pendingImage && !props.project
                    ? <PendingImage file={props.pendingImage} className="size-8 rounded-[6px] object-cover" />
                    : <ProjectIcon project={{ id: props.project?.id ?? 0, color: props.color, icon: props.value || null,
                        icon_image_version: props.project?.icon_image_version }} size={24} />}
            </span>
            <span className="flex min-w-0 flex-1 flex-col items-start gap-0.5">
                <span className="truncate text-[13px]">{hasImage ? "Свой логотип" : selected?.label ?? (projectEmoji(props.value) ? "Эмодзи" : "Цвет проекта")}</span>
                <span className="text-[11px] text-muted">Логотип, иконка и цвет</span>
            </span>
            <Palette size={16} className="shrink-0 text-muted" aria-hidden="true" />
        </Button>
        {open && <AppearanceDialog {...props} onClose={() => setOpen(false)} />}
    </>;
}

function AppearanceDialog({ value, color, onChange, project, onProjectChange, pendingImage, onPendingImageChange, onClose }: ProjectIconPickerProps & { onClose: () => void }) {
    const [draft, setDraft] = useState<Appearance>({ icon: value, color });
    const [file, setFile] = useState<File | null>(pendingImage ?? null);
    const [removeSaved, setRemoveSaved] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const saving = useRef(false);
    const input = useRef<HTMLInputElement>(null);
    const selected = findProjectIcon(draft.icon);
    const emoji = projectEmoji(draft.icon) ?? "";
    const icons = selected && !PROJECT_PICKER_ICONS.some(option => option.name === selected.name)
        ? [selected, ...PROJECT_PICKER_ICONS] : PROJECT_PICKER_ICONS;
    const hasImage = Boolean(file || (project?.icon_image_version && !removeSaved));
    const canPickImage = Boolean(project || onPendingImageChange);
    const previewProject = { id: project?.id ?? 0, color: draft.color, icon: draft.icon || null,
        icon_image_version: removeSaved ? null : project?.icon_image_version };

    function chooseImage(chosen: File) {
        const problem = iconImageProblem(chosen);
        if (problem) { setError(problem); return; }
        setFile(chosen);
        setError(null);
    }

    async function apply() {
        if (saving.current) return;
        saving.current = true;
        setBusy(true);
        setError(null);
        try {
            if (project && file) {
                const body = new FormData(); body.append("file", file);
                const saved = await api.postForm<Project>(endpoints.projectIconImage(project.id), body);
                onProjectChange?.(saved);
            } else if (project && removeSaved && project.icon_image_version) {
                const saved = await api.delete<Project>(endpoints.projectIconImage(project.id));
                onProjectChange?.(saved);
            } else if (!project) {
                onPendingImageChange?.(file);
            }
            onChange(draft);
            onClose();
        } catch (cause) {
            setError((cause as Error).message || "Не удалось сохранить картинку. Повторите попытку.");
        } finally {
            saving.current = false;
            setBusy(false);
        }
    }

    return <Modal title="Оформление проекта" description="Загрузите свой логотип или выберите иконку и цвет."
        isOpen size="md" isDismissable={!busy} onOpenChange={isOpen => { if (!isOpen && !saving.current) onClose(); }}
        footer={<><Button onClick={onClose} disabled={busy}>Отмена</Button>
            <Button variant="primary" onClick={() => void apply()} disabled={busy}
                icon={busy ? <Loader2 size={14} className="animate-spin motion-reduce:animate-none" /> : undefined}>
                {busy ? "Сохранение…" : "Применить"}</Button></>}>
        <fieldset disabled={busy} className="flex min-w-0 flex-col gap-5">
            <div className="flex min-w-0 items-center gap-4 rounded-[var(--radius-card)] border border-line-subtle bg-surface/40 p-4">
                <span className="grid size-16 shrink-0 place-items-center overflow-hidden rounded-[10px] border border-line-subtle bg-surface" data-testid="appearance-preview">
                    {file ? <PendingImage key={`${file.name}:${file.lastModified}`} file={file} className="size-16 object-cover" />
                        : <ProjectIcon project={previewProject} size={48} />}
                </span>
                <span className="flex min-w-0 flex-col gap-1">
                    <span className="text-[13px] font-medium text-primary">{hasImage ? "Свой логотип" : selected?.label ?? (emoji ? "Эмодзи" : "Цвет проекта")}</span>
                    <span className="text-[12px] text-muted">Так проект будет выглядеть в списках и шапке.</span>
                </span>
            </div>
            {canPickImage && <section className="flex flex-col gap-2" aria-label="Своя картинка">
                <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" tabIndex={-1}
                    aria-label="Файл картинки проекта" onChange={event => {
                        const chosen = event.target.files?.[0]; event.target.value = "";
                        if (chosen) chooseImage(chosen);
                    }} />
                <div className="flex flex-wrap gap-2">
                    <Button icon={<ImageUp size={15} />} onClick={() => input.current?.click()}>
                        {hasImage ? "Заменить картинку" : "Выбрать картинку"}</Button>
                    {hasImage && <Button variant="ghost" icon={<X size={14} />} onClick={() => {
                        setFile(null); setRemoveSaved(true); setError(null);
                    }}>Убрать картинку</Button>}
                </div>
                <p className="text-[12px] text-muted">{ICON_IMAGE_HINT}. {project ? "Картинка сохранится при применении." : "Картинка загрузится после создания проекта."}</p>
                {hasImage && <p className="text-[12px] text-muted">Картинка показывается вместо иконки. После её удаления вернётся выбранная иконка.</p>}
            </section>}
            {error && <p role="alert" className="text-[12px] text-danger">{error}</p>}
            <section className="flex min-w-0 flex-col gap-2" aria-label="Иконки набора">
                <h3 className="text-xs font-medium text-secondary">Иконка</h3>
                <div className="grid grid-cols-[repeat(auto-fit,minmax(44px,1fr))] gap-1.5">
                    {icons.map(({ name, label, Icon }) => {
                        const chosen = selected?.name === name;
                        return <button key={name} type="button" title={label} aria-label={label} aria-pressed={chosen}
                            onClick={() => setDraft({ ...draft, icon: lucideIconValue(name) })}
                            className={cn("relative grid size-11 place-items-center rounded-[8px] border text-secondary transition-colors hover:bg-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
                                chosen ? "border-accent bg-accent-soft" : "border-line-subtle bg-surface/30")}
                            style={chosen ? { color: draft.color } : undefined}>
                            <Icon size={22} strokeWidth={1.8} aria-hidden="true" />
                            {chosen && <Check size={10} className="absolute right-0.5 top-0.5 text-primary" aria-hidden="true" />}
                        </button>;
                    })}
                </div>
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <Input aria-label="Эмодзи вместо иконки" placeholder="Свой эмодзи" maxLength={8} value={emoji}
                        onChange={event => setDraft({ ...draft, icon: event.target.value })} className="h-9 min-w-0 flex-1 basis-32" />
                    <Button variant="ghost" onClick={() => setDraft({ ...draft, icon: "" })} disabled={!draft.icon}>Без иконки</Button>
                </div>
            </section>
            <section className="flex flex-col gap-2" aria-label="Цвет проекта">
                <h3 className="text-xs font-medium text-secondary">Цвет проекта</h3>
                <div className="flex flex-wrap gap-1">
                    {PROJECT_COLORS.map(shade => <button key={shade} type="button" aria-label={`Цвет ${shade}`} aria-pressed={draft.color === shade}
                        onClick={() => setDraft({ ...draft, color: shade })}
                        className="grid size-11 shrink-0 place-items-center rounded-[8px] hover:bg-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
                        <span data-testid="project-color-swatch" className={cn("grid size-6 shrink-0 place-items-center rounded-full", draft.color === shade && "ring-2 ring-primary ring-offset-2 ring-offset-surface")}
                            style={{ backgroundColor: shade }}>{draft.color === shade && <Check size={14} strokeWidth={2.5} className="text-black/80" aria-hidden="true" />}</span>
                    </button>)}
                </div>
            </section>
        </fieldset>
    </Modal>;
}
