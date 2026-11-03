import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Download, LogOut, Menu, Plus, UserRound } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { GLOBAL_NAV_ITEMS } from "@/lib/navigation";
import { useCurrentUser, useLogout } from "@/lib/useAuth";
import { fullName, type Project } from "@/lib/types";
import { cn } from "@/lib/cn";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { ProjectIcon } from "@/components/projects/ProjectIcon";
import { IconButton, Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";

const item = (active: boolean) => cn("flex min-h-12 min-w-0 items-center gap-3 rounded-md px-3 py-2 text-[14px]", active ? "bg-accent-soft text-accent" : "text-secondary hover:bg-hover");

export function MobileMenu() {
    const [open, setOpen] = useState(false);
    const { data: user } = useCurrentUser();
    const logout = useLogout();
    const navigate = useNavigate();
    const { data: projects } = useQuery({ queryKey: queryKeys.projects, queryFn: () => api.get<Project[]>(endpoints.projects()) });
    const close = () => setOpen(false);
    return <>
        <IconButton label="Открыть меню" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)} className="ml-auto">
            <Menu size={22} />
        </IconButton>
        <Drawer label="Меню навигации" isOpen={open} onClose={close} closeLabel="Закрыть меню" header={<BrandLogo variant="logo" className="mt-1 w-44" />}>
            <nav aria-label="Основная навигация" className="grid gap-1 p-3">
                {GLOBAL_NAV_ITEMS.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} className={({ isActive }) => item(isActive)} onClick={close}>
                    <Icon size={20} className="shrink-0" aria-hidden="true" /><span>{label}</span>
                </NavLink>)}
            </nav>
            <nav aria-label="Мои проекты" className="border-t border-line-subtle p-3">
                <p className="px-3 py-2 text-[12px] font-semibold text-muted">Проекты</p>
                {projects?.map(project => <NavLink key={project.id} to={`/projects/${project.key}`} onClick={close} className={({ isActive }) => item(isActive)}>
                    <ProjectIcon project={project} size={20} /><span className="truncate">{project.name}</span>
                </NavLink>)}
                <NavLink to="/projects/new" onClick={close} className={({ isActive }) => item(isActive)}><Plus size={20} aria-hidden="true" />Новый проект</NavLink>
            </nav>
            <div className="flex flex-col gap-1 border-t border-line-subtle p-3">
                <NavLink to="/profile" onClick={close} className={({ isActive }) => item(isActive)}><UserRound size={20} className="shrink-0" aria-hidden="true" /><span className="truncate">{user ? fullName(user) : "Профиль"}</span></NavLink>
                <NavLink to="/profile#install" onClick={close} className="flex min-h-12 items-center gap-3 rounded-md px-3 text-[14px] text-secondary hover:bg-hover"><Download size={20} aria-hidden="true" />На главный экран</NavLink>
                <Button className="mt-2 justify-start" variant="ghost" icon={<LogOut size={20} />} disabled={logout.isPending} onClick={() => logout.mutate(undefined, { onSettled: () => { close(); navigate("/login"); } })}>Выйти</Button>
            </div>
        </Drawer>
    </>;
}
