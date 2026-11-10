import { useRef } from "react";
import { Button, ListBox, ListBoxItem, Popover, Select, SelectValue } from "react-aria-components";
import { Check, ChevronDown, FolderKanban, X } from "lucide-react";
import type { Project } from "@/lib/types";
import { IconButton } from "@/components/ui/Button";
import { ProjectIcon } from "@/components/projects/ProjectIcon";

interface MyTaskProjectFilterProps {
    projects: Project[];
    projectId?: number;
    onChange: (projectId: number | undefined) => void;
    disabled?: boolean;
    loading?: boolean;
}

const optionClass = "flex min-h-9 cursor-pointer items-center gap-2.5 rounded-[var(--radius-control)] px-2.5 py-2 text-[13px] text-secondary outline-none data-[focused]:bg-hover data-[selected]:bg-accent-soft data-[selected]:text-primary";

export function MyTaskProjectFilter({ projects, projectId, onChange, disabled, loading }: MyTaskProjectFilterProps) {
    const trigger = useRef<HTMLButtonElement>(null);
    const selected = projects.find(project => project.id === projectId);
    const label = selected?.name ?? (projectId === undefined ? "Все проекты" : `Проект ${projectId}`);

    return <div className="inline-flex min-w-0 max-w-full items-center gap-0.5">
        <Select aria-label="Фильтр по проекту" value={projectId ?? "all"} isDisabled={disabled}
            onChange={key => onChange(key === "all" || key === null ? undefined : Number(key))}>
            <Button ref={trigger} aria-busy={loading}
                className="material-metal touch-target group inline-flex h-8 max-w-[220px] items-center gap-2 rounded-[var(--radius-control)] border border-line px-2.5 text-[13px] font-medium text-secondary outline-none transition-colors hover:border-line-strong hover:bg-hover hover:text-primary data-[focus-visible]:border-accent/70 data-[focus-visible]:shadow-focus data-[disabled]:cursor-not-allowed data-[disabled]:opacity-45">
                {selected ? <ProjectIcon project={selected} size={14} /> : <FolderKanban size={14} aria-hidden="true" className="shrink-0 text-muted" />}
                <SelectValue className="min-w-0 truncate">{() => label}</SelectValue>
                <ChevronDown size={13} aria-hidden="true" className="shrink-0 text-muted group-data-[pressed]:text-primary" />
            </Button>
            <Popover placement="bottom start" offset={6}
                className="material-glass z-[60] w-[320px] max-w-[calc(100vw-24px)] overflow-hidden rounded-[var(--radius-panel)] p-1.5 shadow-panel">
                <ListBox aria-label="Доступные проекты" className="max-h-[min(320px,calc(100dvh-96px))] overflow-y-auto overscroll-contain outline-none">
                    <ListBoxItem id="all" textValue="Все проекты" aria-label="Все проекты" className={optionClass}>
                        {({ isSelected }) => <><FolderKanban size={14} aria-hidden="true" className="shrink-0 text-muted" />
                            <span className="min-w-0 flex-1">Все проекты</span>{isSelected && <Check size={14} aria-hidden="true" className="shrink-0 text-accent" />}</>}
                    </ListBoxItem>
                    {projects.map(project => <ListBoxItem key={project.id} id={project.id} textValue={`${project.key} · ${project.name}`}
                        aria-label={`${project.key} · ${project.name}`} className={optionClass}>
                        {({ isSelected }) => <><ProjectIcon project={project} size={14} />
                            <span className="min-w-0 flex-1 break-words">{project.name}</span>
                            <span className="shrink-0 font-mono text-[11px] text-muted">{project.key}</span>
                            {isSelected && <Check size={14} aria-hidden="true" className="shrink-0 text-accent" />}</>}
                    </ListBoxItem>)}
                </ListBox>
            </Popover>
        </Select>
        {projectId !== undefined && <IconButton label="Сбросить фильтр проекта" disabled={disabled}
            onClick={() => { onChange(undefined); requestAnimationFrame(() => trigger.current?.focus()); }} className="text-muted">
            <X size={14} aria-hidden="true" />
        </IconButton>}
    </div>;
}
