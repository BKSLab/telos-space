import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChartNoAxesCombined, FolderKanban, Plus } from "lucide-react";
import { Page } from "@/components/layout/AppShell";
import { PortfolioSummary } from "@/components/dashboard/PortfolioSummary";
import { ProjectCatalog } from "@/components/projects/ProjectCatalog";
import { LinkButton } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { useUiStore } from "@/stores/ui";

const VIEWS = [
    { id: "projects", label: "Проекты", icon: FolderKanban },
    { id: "summary", label: "Сводка", icon: ChartNoAxesCombined },
] as const;

export function PortfolioPage() {
    const [params, setParams] = useSearchParams();
    const storedView = useUiStore(state => state.portfolioView);
    const setView = useUiStore(state => state.setPortfolioView);
    const requested = params.get("view");
    const view = requested === "projects" || requested === "summary" ? requested : storedView;

    useEffect(() => {
        if (requested !== "projects" && requested !== "summary") {
            setParams(current => {
                const next = new URLSearchParams(current);
                next.set("view", view);
                return next;
            }, { replace: true });
        } else if (view !== storedView) setView(view);
    }, [requested, view, storedView, setView, setParams]);

    return <Page className="max-w-[1600px]">
        <header className="flex flex-wrap items-end justify-between gap-3">
            <div className="flex min-w-0 flex-col gap-1.5">
                <h1 className="text-[24px] font-semibold tracking-[-0.03em] text-primary">Портфель</h1>
                <p className="text-[14px] text-muted">{view === "projects" ? "Все проекты и их текущее состояние" : "Общая картина и ближайшие действия"}</p>
            </div>
            <LinkButton to="/projects/new" variant="primary" icon={<Plus size={16} aria-hidden="true" />}>Новый проект</LinkButton>
        </header>
        <nav aria-label="Представления портфеля" className="-mt-1 flex gap-1 border-b border-line">
            {VIEWS.map(({ id, label, icon: Icon }) => {
                const next = new URLSearchParams(params);
                next.set("view", id);
                return <Link key={id} to={`/?${next}`} aria-current={view === id ? "page" : undefined}
                    className={cn("brand-tab -mb-px flex min-h-11 items-center gap-2 border-b px-4 py-2.5 text-[14px] font-medium focus-visible:outline-2 focus-visible:outline-accent", view === id ? "border-accent bg-accent/[0.075] text-primary" : "border-transparent text-muted hover:bg-hover hover:text-primary")}>
                    <Icon size={17} aria-hidden="true" />{label}
                </Link>;
            })}
        </nav>
        {view === "projects" ? <ProjectCatalog /> : <PortfolioSummary />}
    </Page>;
}
