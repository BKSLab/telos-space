import { useState } from "react";
import { apiUrl, endpoints } from "@/lib/api";
import { cn } from "@/lib/cn";
import { findProjectIcon, projectEmoji } from "@/lib/projectIcons";
import { StatusDot } from "@/components/ui/Badge";

export interface ProjectIconSource {
    id: number;
    color: string;
    icon: string | null;
    icon_image_version?: string | null;
}

interface ProjectIconProps {
    project: ProjectIconSource;
    /** Размер иконки в px (картинка — квадрат этого размера). */
    size?: number;
    /** `color` — линия иконки цветом проекта; `current` — цветом текста (тёмный фон сайдбара). */
    tone?: "color" | "current";
    className?: string;
    /** Классы цветной точки, если у проекта нет ни картинки, ни иконки. */
    dotClassName?: string;
}

/**
 * Единое изображение проекта: своя картинка → иконка из набора → эмодзи →
 * цветная точка. Картинка отдаётся трекером после проверки доступа; если она
 * не загрузилась, показывается следующий вариант.
 */
export function ProjectIcon({
    project,
    size = 16,
    tone = "color",
    className,
    dotClassName,
}: ProjectIconProps) {
    const version = project.icon_image_version ?? null;
    const [brokenVersion, setBrokenVersion] = useState<string | null>(null);

    if (version && brokenVersion !== version) {
        return (
            <img
                src={apiUrl(endpoints.projectIconImage(project.id, version))}
                alt=""
                aria-hidden="true"
                width={size}
                height={size}
                loading="lazy"
                decoding="async"
                onError={() => setBrokenVersion(version)}
                className={cn("shrink-0 rounded-[4px] object-cover", className)}
                style={{ width: size, height: size }}
                data-testid="project-icon-image"
            />
        );
    }

    const option = findProjectIcon(project.icon);
    if (option) {
        const { Icon } = option;
        return (
            <Icon
                size={size}
                strokeWidth={2}
                aria-hidden="true"
                className={cn("shrink-0", className)}
                style={tone === "color" ? { color: project.color } : undefined}
                data-testid="project-icon-lucide"
                data-icon={option.name}
            />
        );
    }

    const emoji = projectEmoji(project.icon);
    if (emoji) {
        return (
            <span
                aria-hidden="true"
                className={cn("shrink-0 leading-none", className)}
                style={{ fontSize: Math.round(size * 0.95) }}
                data-testid="project-icon-emoji"
            >
                {emoji}
            </span>
        );
    }

    return <span aria-hidden="true" className={cn("inline-flex shrink-0 items-center justify-center", className)} style={{ width: size, height: size }}>
        <StatusDot color={project.color} className={cn(size >= 24 && "size-2.5", dotClassName)} />
    </span>;
}
