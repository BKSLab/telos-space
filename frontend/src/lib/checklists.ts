import { newUuid } from "@/lib/uuid";

export interface ChecklistItem {
    id: string;
    text: string;
    is_completed: boolean;
}

export interface TaskChecklist {
    title: string;
    items: ChecklistItem[];
}

export interface ChecklistSuggestion {
    checklist: TaskChecklist;
    warnings: string[];
}

export function newChecklistItem(): ChecklistItem {
    return { id: newUuid(), text: "", is_completed: false };
}

export function isChecklistValid(value: TaskChecklist | null): boolean {
    return value === null || (
        value.title.trim().length > 0 && value.title.trim().length <= 120 &&
        !value.title.includes("\0") && value.items.length <= 100 &&
        new Set(value.items.map(item => item.id)).size === value.items.length &&
        value.items.every(item => item.text.trim().length > 0 && item.text.trim().length <= 500 && !item.text.includes("\0"))
    );
}

export function checklistInput(value: TaskChecklist | null): TaskChecklist | null {
    return value === null ? null : {
        title: value.title.trim(),
        items: value.items.map(item => ({ id: item.id, text: item.text.trim(), is_completed: item.is_completed })),
    };
}

export function moveChecklistItem(value: TaskChecklist, index: number, offset: -1 | 1): TaskChecklist {
    const target = index + offset;
    if (target < 0 || target >= value.items.length) return value;
    const items = [...value.items];
    [items[index], items[target]] = [items[target], items[index]];
    return { ...value, items };
}
