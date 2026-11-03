import { Palette } from "lucide-react";
import { Select } from "@/components/ui/Field";
import { COLOR_MODE_LABELS, type ColorMode } from "@/lib/calendarGrid";

/** «Цвет по: …» — чем окрашены карточки задач в сетке и полосы на шкале. */
export function ColorModeSelect({ value, modes, onChange }: { value: ColorMode; modes: ColorMode[]; onChange: (mode: ColorMode) => void }) {
    return (
        <label className="flex items-center gap-1 text-[11px] whitespace-nowrap text-muted" title="Чем окрашены задачи: полоска у карточек в сетке и полосы на шкале (Ганте)">
            <Palette size={13} aria-hidden="true" />
            <span className="hidden sm:inline">Цвет по</span>
            <Select
                aria-label="Цвет карточек по"
                value={value}
                onChange={(event) => onChange(event.target.value as ColorMode)}
                className="h-11 w-auto py-0 text-[16px] lg:h-7 lg:text-[12px]"
            >
                {modes.map((mode) => (
                    <option key={mode} value={mode}>
                        {COLOR_MODE_LABELS[mode]}
                    </option>
                ))}
            </Select>
        </label>
    );
}
