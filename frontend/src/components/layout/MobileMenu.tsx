import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { Download, LogOut, Menu, UserRound } from "lucide-react";
import { GLOBAL_NAV_ITEMS, HELP_NAV_ITEM } from "@/lib/navigation";
import { useCurrentUser, useLogout } from "@/lib/useAuth";
import { fullName } from "@/lib/types";
import { cn } from "@/lib/cn";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { ProjectNavigation } from "@/components/layout/ProjectNavigation";
import { IconButton, Button } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";

const item = (active: boolean) => cn("flex min-h-12 min-w-0 items-center gap-3 rounded-md px-3 py-2 text-[14px]", active ? "bg-accent-soft text-accent" : "text-secondary hover:bg-hover");

export function MobileMenu() {
    const [open, setOpen] = useState(false);
    const { data: user } = useCurrentUser();
    const logout = useLogout();
    const navigate = useNavigate();
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
            <div className="px-3 pb-3"><ProjectNavigation mobile onNavigate={close} /></div>
            <div className="flex flex-col gap-1 border-t border-line-subtle p-3">
                <NavLink to={HELP_NAV_ITEM.to} onClick={close} className={({ isActive }) => item(isActive)}><HELP_NAV_ITEM.icon size={20} aria-hidden="true" />{HELP_NAV_ITEM.label}</NavLink>
                <NavLink to="/profile" onClick={close} className={({ isActive }) => item(isActive)}><UserRound size={20} className="shrink-0" aria-hidden="true" /><span className="truncate">{user ? fullName(user) : "Профиль"}</span></NavLink>
                <NavLink to="/profile#install" onClick={close} className="flex min-h-12 items-center gap-3 rounded-md px-3 text-[14px] text-secondary hover:bg-hover"><Download size={20} aria-hidden="true" />На главный экран</NavLink>
                <Button className="mt-2 justify-start" variant="ghost" icon={<LogOut size={20} />} disabled={logout.isPending} onClick={() => logout.mutate(undefined, { onSettled: () => { close(); navigate("/login"); } })}>Выйти</Button>
            </div>
        </Drawer>
    </>;
}
