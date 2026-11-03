import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { FolderKanban, LayoutDashboard } from "lucide-react";
import { cn } from "@/lib/cn";
import { Sidebar } from "@/components/layout/Sidebar";
import { ToastViewport } from "@/components/ui/Toast";
import { TaskDialog } from "@/components/tasks/TaskDialog";
import { RiskDrawer } from "@/components/risks/RiskDrawer";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { MobileMenu } from "@/components/layout/MobileMenu";

const MOBILE_ITEMS = [
    { to: "/", label: "Портфель", icon: LayoutDashboard, end: true },
    { to: "/projects", label: "Проекты", icon: FolderKanban, end: false },
];

/**
 * Каркас приложения: сворачиваемый сайдбар на десктопе и компактная
 * навигация на узких экранах (раздел 19 дизайн-гайда).
 */
export function AppShell({ children }: { children: ReactNode }) {
    return (
        <div className="app-shell flex h-full min-w-0 overflow-hidden bg-app lg:gap-2 lg:p-2">
            <a
                href="#main-content"
                className={cn(
                    "sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[70]",
                    "focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-[13px]",
                    "focus:font-semibold focus:text-on-accent",
                )}
            >
                Перейти к содержимому
            </a>

            <Sidebar />

            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                <nav
                    aria-label="Разделы"
                    className="mobile-header material-metal flex min-h-14 shrink-0 items-center gap-0.5 border-b border-line-subtle px-2 lg:hidden"
                >
                    <NavLink to="/" className="mr-1 flex size-11 shrink-0 items-center justify-center rounded focus-visible:outline-2 focus-visible:outline-accent">
                        <BrandLogo variant="symbol" className="size-7" />
                    </NavLink>
                    {MOBILE_ITEMS.map(({ to, label, icon: Icon, end }) => (
                        <NavLink
                            key={to}
                            to={to}
                            end={end}
                            className={({ isActive }) =>
                                cn(
                                    "brand-tab flex min-h-11 items-center gap-1.5 rounded-md px-2 py-1.5 text-[13px]",
                                    isActive
                                        ? "bg-accent-soft text-accent"
                                        : "text-secondary hover:bg-hover hover:text-primary",
                                )
                            }
                        >
                            <Icon size={15} aria-hidden="true" />
                            {label}
                        </NavLink>
                    ))}
                    <MobileMenu />
                </nav>

                <main id="main-content" className="app-workspace material-mineral min-h-0 min-w-0 flex-1 overflow-hidden lg:rounded-[var(--radius-panel)] lg:border lg:border-line">
                    {children}
                </main>
            </div>

            <ToastViewport />
            <TaskDialog />
            <RiskDrawer />
        </div>
    );
}

interface PageProps {
    children: ReactNode;
    className?: string;
}

/** Обычная прокручиваемая страница с внутренними отступами. */
export function Page({ children, className }: PageProps) {
    return (
        <div className="scrollbar-thin h-full min-w-0 overflow-x-hidden overflow-y-auto overscroll-y-contain">
            <div className={cn("mx-auto flex w-full min-w-0 max-w-6xl flex-col gap-5 px-3 py-4 pb-24 sm:gap-6 sm:px-6 sm:py-6", className)}>
                {children}
            </div>
        </div>
    );
}
