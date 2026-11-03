import { useId, useState, type ReactNode } from "react";
import { ChevronDown, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

/** Keep the work visible on a phone; the same filters stay expanded on desktop. */
export function ResponsiveFilters({ children, active = false }: { children: ReactNode; active?: boolean }) {
    const [open, setOpen] = useState(false);
    const id = useId();
    return <div className="min-w-0">
        <Button className="w-full justify-between md:hidden" icon={<SlidersHorizontal size={16} />} aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
            <span className="mr-auto">Фильтры{active ? " · применены" : ""}</span><ChevronDown size={16} className={cn(open && "rotate-180")} />
        </Button>
        <div id={id} className={cn("md:block", open ? "mt-3 md:mt-0" : "hidden")}>{children}</div>
    </div>;
}
