import { useMemo, useState } from "react";
import type { WbsNode } from "@/lib/types";
import { collectSubtreeIds, flattenTree, type WbsTreeNode } from "@/lib/wbsTree";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Field, Input, Select } from "@/components/ui/Field";

interface TransferTaskDialogProps {
    taskKey: string;
    roots: WbsTreeNode[];
    currentNodeId: number | null;
    isOpen: boolean;
    onClose: () => void;
    onSubmit: (wbsNodeId: number | null) => void;
}

/** Перенос задачи в раздел без мыши — обязательная альтернатива drag & drop. */
export function TransferTaskDialog({
    taskKey,
    roots,
    currentNodeId,
    isOpen,
    onClose,
    onSubmit,
}: TransferTaskDialogProps) {
    const [search, setSearch] = useState("");
    const [selected, setSelected] = useState<string>(
        currentNodeId === null ? "" : String(currentNodeId),
    );
    const options = useFilteredSections(roots, search);

    return (
        <Modal
            title={`Переместить ${taskKey}`}
            description="Выберите раздел ИСР или верните задачу в пул нераспределённых."
            isOpen={isOpen}
            onOpenChange={(open) => {
                if (!open) {
                    onClose();
                }
            }}
            footer={
                <>
                    <Button onClick={onClose}>Отмена</Button>
                    <Button
                        variant="primary"
                        onClick={() => {
                            onSubmit(selected === "" ? null : Number(selected));
                            onClose();
                        }}
                    >
                        Переместить
                    </Button>
                </>
            }
        >
            <div className="flex flex-col gap-3">
                <Field label="Поиск раздела">
                    {(id) => (
                        <Input
                            id={id}
                            autoFocus
                            value={search}
                            placeholder="Название или номер"
                            onChange={(event) => setSearch(event.target.value)}
                        />
                    )}
                </Field>
                <Field label="Раздел">
                    {(id) => (
                        <Select
                            id={id}
                            size={Math.min(Math.max(options.length + 1, 4), 12)}
                            value={selected}
                            className="h-auto py-1"
                            onChange={(event) => setSelected(event.target.value)}
                        >
                            <option value="">— Не в структуре (пул) —</option>
                            {options.map((section) => (
                                <option key={section.node.id} value={section.node.id}>
                                    {`${"  ".repeat(section.depth)}${section.number} ${section.node.title}`}
                                </option>
                            ))}
                        </Select>
                    )}
                </Field>
            </div>
        </Modal>
    );
}

interface MoveNodeDialogProps {
    nodes: WbsNode[];
    roots: WbsTreeNode[];
    section: WbsTreeNode;
    isOpen: boolean;
    onClose: () => void;
    onSubmit: (parentId: number | null, beforeId: number | null) => void;
}

/** Перенос раздела: выбор нового родителя и соседа, перед которым встать. */
export function MoveNodeDialog({
    nodes,
    roots,
    section,
    isOpen,
    onClose,
    onSubmit,
}: MoveNodeDialogProps) {
    const [parentId, setParentId] = useState<string>(
        section.node.parent_id === null ? "" : String(section.node.parent_id),
    );
    const [beforeId, setBeforeId] = useState<string>("");

    // Раздел нельзя перенести внутрь самого себя или собственного потомка.
    const forbidden = useMemo(
        () => collectSubtreeIds(nodes, section.node.id),
        [nodes, section.node.id],
    );
    const parentOptions = useMemo(
        () => flattenTree(roots).filter((item) => !forbidden.has(item.node.id)),
        [roots, forbidden],
    );
    const siblingOptions = useMemo(() => {
        const targetParent = parentId === "" ? null : Number(parentId);
        return flattenTree(roots).filter(
            (item) => item.node.parent_id === targetParent && item.node.id !== section.node.id,
        );
    }, [roots, parentId, section.node.id]);

    return (
        <Modal
            title={`Переместить раздел «${section.node.title}»`}
            description="Позицию внутри уровня рассчитает сервер: укажите родителя и соседа."
            isOpen={isOpen}
            onOpenChange={(open) => {
                if (!open) {
                    onClose();
                }
            }}
            footer={
                <>
                    <Button onClick={onClose}>Отмена</Button>
                    <Button
                        variant="primary"
                        onClick={() => {
                            onSubmit(
                                parentId === "" ? null : Number(parentId),
                                beforeId === "" ? null : Number(beforeId),
                            );
                            onClose();
                        }}
                    >
                        Переместить
                    </Button>
                </>
            }
        >
            <div className="flex flex-col gap-3">
                <Field label="Родительский раздел">
                    {(id) => (
                        <Select
                            id={id}
                            value={parentId}
                            onChange={(event) => {
                                setParentId(event.target.value);
                                setBeforeId("");
                            }}
                        >
                            <option value="">— Верхний уровень —</option>
                            {parentOptions.map((item) => (
                                <option key={item.node.id} value={item.node.id}>
                                    {`${"  ".repeat(item.depth)}${item.number} ${item.node.title}`}
                                </option>
                            ))}
                        </Select>
                    )}
                </Field>

                <Field label="Встать перед" hint="Без выбора раздел встанет в конец уровня">
                    {(id) => (
                        <Select
                            id={id}
                            value={beforeId}
                            onChange={(event) => setBeforeId(event.target.value)}
                        >
                            <option value="">— В конец —</option>
                            {siblingOptions.map((item) => (
                                <option key={item.node.id} value={item.node.id}>
                                    {`${item.number} ${item.node.title}`}
                                </option>
                            ))}
                        </Select>
                    )}
                </Field>
            </div>
        </Modal>
    );
}

function useFilteredSections(roots: WbsTreeNode[], search: string): WbsTreeNode[] {
    return useMemo(() => {
        const query = search.trim().toLowerCase();
        const flat = flattenTree(roots);
        if (query === "") {
            return flat;
        }
        return flat.filter(
            (item) =>
                item.node.title.toLowerCase().includes(query) || item.number.startsWith(query),
        );
    }, [roots, search]);
}
