import { useState, type CSSProperties } from "react";
import { apiUrl, endpoints } from "@/lib/api";
import { cn } from "@/lib/cn";
import { findProjectIcon, projectEmoji } from "@/lib/projectIcons";
import "./ProjectIdentity.css";

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
}

/**
 * Единое изображение проекта: своя картинка → иконка из набора → эмодзи →
 * знак «Пик и цель». Картинка отдаётся трекером после проверки доступа; если она
 * не загрузилась, показывается следующий вариант.
 */
export function ProjectIcon({
    project,
    size = 16,
    tone = "color",
    className,
}: ProjectIconProps) {
    const version = project.icon_image_version ?? null;
    const imageIdentity = version ? `${project.id}:${version}` : null;
    const [brokenImage, setBrokenImage] = useState<string | null>(null);
    // В обзорной шапке слот адаптируется через CSS; остальные размеры сохраняются.
    const dimensions = {
        width: `var(--project-icon-size, ${size}px)`,
        height: `var(--project-icon-size, ${size}px)`,
    };

    if (version && brokenImage !== imageIdentity) {
        return (
            <img
                src={apiUrl(endpoints.projectIconImage(project.id, version))}
                alt=""
                aria-hidden="true"
                width={size}
                height={size}
                loading="lazy"
                decoding="async"
                onError={() => setBrokenImage(imageIdentity)}
                className={cn("shrink-0 rounded-[4px] object-cover", className)}
                style={dimensions}
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
                style={{ ...dimensions, ...(tone === "color" ? { color: project.color } : {}) }}
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
                className={cn("inline-flex shrink-0 items-center justify-center leading-none", className)}
                style={{ ...dimensions, fontSize: `calc(var(--project-icon-size, ${size}px) * var(--project-icon-emoji-scale, 0.95))` }}
                data-testid="project-icon-emoji"
            >
                {emoji}
            </span>
        );
    }

    return <span aria-hidden="true" className={cn("project-default-icon shrink-0", className)}
        style={{ ...dimensions, "--project-icon-color": project.color } as CSSProperties}
        data-testid="project-icon-default" data-icon="peak-goal">
        <span className="project-default-icon-peak" />
    </span>;
}
