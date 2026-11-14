// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "@/lib/api";
import type { Project } from "@/lib/types";
import { findProjectIcon, PROJECT_PICKER_ICONS } from "@/lib/projectIcons";
import { ProjectIconPicker } from "./ProjectIconPicker";

const savedProject = { id: 1, key: "DEMO", name: "Проект", color: "#58a6ff", icon: null, icon_image_version: "v1" } as Project;
let nextUrl = 0;
beforeEach(() => {
    vi.stubGlobal("URL", class extends URL {
        static createObjectURL = vi.fn(() => `blob:project-${++nextUrl}`);
        static revokeObjectURL = vi.fn();
    });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function setup({ icon = "", project, pending = null }: { icon?: string; project?: Project; pending?: File | null } = {}) {
    const changed = vi.fn(), pendingChanged = vi.fn(), projectChanged = vi.fn();
    function Host() {
        const [appearance, setAppearance] = useState({ icon, color: "#58a6ff" });
        const [image, setImage] = useState<File | null>(pending);
        return <><input aria-label="Название проекта" defaultValue="Черновик проекта" />
            <ProjectIconPicker value={appearance.icon} color={appearance.color} project={project}
                onChange={value => { changed(value); setAppearance(value); }}
                pendingImage={image} onPendingImageChange={file => { pendingChanged(file); setImage(file); }}
                onProjectChange={projectChanged} /></>;
    }
    const view = render(<Host />);
    return { ...view, user: userEvent.setup(), changed, pendingChanged, projectChanged };
}
async function open(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("button", { name: "Оформление проекта" }));
    return screen.findByRole("dialog", { name: "Оформление проекта" });
}

it("показывает компактный нейтральный набор; применение меняет иконку и цвет одним событием", async () => {
    const { user, changed } = setup();
    const dialog = await open(user);
    expect(PROJECT_PICKER_ICONS).toHaveLength(18);
    await user.click(within(dialog).getByRole("button", { name: "Запуск" }));
    await user.click(within(dialog).getByRole("button", { name: "Цвет #a371f7" }));
    expect(changed).not.toHaveBeenCalled();
    expect(within(dialog).getByTestId("appearance-preview").querySelector('[data-icon="rocket"]')).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Применить" }));
    expect(changed).toHaveBeenCalledExactlyOnceWith({ icon: "lucide:rocket", color: "#a371f7" });
    expect(screen.queryByRole("dialog")).toBeNull();
});

it("отмена и Escape отбрасывают выбор, сохраняют поля проекта и возвращают фокус", async () => {
    const { user, changed } = setup({ icon: "lucide:target" });
    const name = screen.getByLabelText("Название проекта");
    await user.type(name, " — ещё текст");
    const dialog = await open(user);
    await user.click(within(dialog).getByRole("button", { name: "Запуск" }));
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(changed).not.toHaveBeenCalled();
    expect((name as HTMLInputElement).value).toBe("Черновик проекта — ещё текст");
    const again = await open(user);
    expect(within(again).getByRole("button", { name: "Цель" }).getAttribute("aria-pressed")).toBe("true");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Оформление проекта" })));
});

it("сохранённая иконка вне короткого набора остаётся доступной и не меняется при выборе цвета", async () => {
    const { user, changed } = setup({ icon: "lucide:ship" });
    const dialog = await open(user);
    expect(findProjectIcon("lucide:ship")?.name).toBe("ship");
    expect(within(dialog).getByRole("button", { name: "Море" }).getAttribute("aria-pressed")).toBe("true");
    await user.click(within(dialog).getByRole("button", { name: "Цвет #db61a2" }));
    await user.click(within(dialog).getByRole("button", { name: "Применить" }));
    expect(changed).toHaveBeenCalledWith({ icon: "lucide:ship", color: "#db61a2" });
});

it("выбранный файл становится pending только после применения; URL освобождаются при отмене и unmount", async () => {
    const { user, pendingChanged, unmount } = setup();
    let dialog = await open(user);
    const file = new File(["image"], "logo.png", { type: "image/png" });
    await user.upload(within(dialog).getByLabelText("Файл картинки проекта"), file);
    expect(within(dialog).getByTestId("icon-picker-pending").getAttribute("src")).toMatch(/^blob:/);
    expect(pendingChanged).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    dialog = await open(user);
    await user.upload(within(dialog).getByLabelText("Файл картинки проекта"), file);
    await user.click(within(dialog).getByRole("button", { name: "Применить" }));
    expect(pendingChanged).toHaveBeenCalledExactlyOnceWith(file);
    expect(screen.getByTestId("icon-picker-pending").getAttribute("src")).toMatch(/^blob:/);
    unmount();
    expect(vi.mocked(URL.revokeObjectURL).mock.calls.length).toBe(vi.mocked(URL.createObjectURL).mock.calls.length);
});

it("загрузка существующего логотипа выполняется при применении; ошибка сохраняет черновик для повтора", async () => {
    const post = vi.spyOn(api, "postForm").mockRejectedValueOnce(new Error("Нет связи"))
        .mockResolvedValueOnce({ ...savedProject, icon_image_version: "v2" });
    const { user, changed, projectChanged } = setup({ project: savedProject });
    const dialog = await open(user);
    await user.upload(within(dialog).getByLabelText("Файл картинки проекта"), new File(["image"], "logo.webp", { type: "image/webp" }));
    expect(post).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Применить" }));
    expect(await within(dialog).findByRole("alert")).toHaveProperty("textContent", "Нет связи");
    expect(changed).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Применить" }));
    await waitFor(() => expect(projectChanged).toHaveBeenCalledWith(expect.objectContaining({ icon_image_version: "v2" })));
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[0][1].get("file")).toBe(post.mock.calls[1][1].get("file"));
});

it("удаление логотипа можно отменить; подтверждённое удаление выполняется один раз", async () => {
    const remove = vi.spyOn(api, "delete").mockResolvedValue({ ...savedProject, icon_image_version: null });
    const { user, projectChanged } = setup({ project: savedProject });
    let dialog = await open(user);
    await user.click(within(dialog).getByRole("button", { name: "Убрать картинку" }));
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(remove).not.toHaveBeenCalled();
    dialog = await open(user);
    await user.click(within(dialog).getByRole("button", { name: "Убрать картинку" }));
    await user.click(within(dialog).getByRole("button", { name: "Применить" }));
    await waitFor(() => expect(projectChanged).toHaveBeenCalledWith(expect.objectContaining({ icon_image_version: null })));
    expect(remove).toHaveBeenCalledExactlyOnceWith("/api/v1/projects/1/icon-image");
});

it("не принимает слишком большой файл и сохраняет уже выбранный pending при отмене", async () => {
    const pending = new File(["image"], "first.png", { type: "image/png" });
    const { user, pendingChanged } = setup({ pending });
    const dialog = await open(user);
    await user.upload(within(dialog).getByLabelText("Файл картинки проекта"), new File([new Uint8Array(2 * 1024 * 1024 + 1)], "big.png", { type: "image/png" }));
    expect(within(dialog).getByRole("alert").textContent).toContain("больше 2 МБ");
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(pendingChanged).not.toHaveBeenCalled();
    expect(screen.getByTestId("icon-picker-pending")).toBeTruthy();
});

it("записывает картинку и без необязательного обработчика обновления проекта", async () => {
    const post = vi.spyOn(api, "postForm").mockResolvedValue(savedProject);
    render(<ProjectIconPicker value="" color="#58a6ff" project={savedProject} onChange={() => {}} />);
    const user = userEvent.setup();
    const dialog = await open(user);
    await user.upload(within(dialog).getByLabelText("Файл картинки проекта"), new File(["image"], "logo.png", { type: "image/png" }));
    await user.click(within(dialog).getByRole("button", { name: "Применить" }));
    await waitFor(() => expect(post).toHaveBeenCalledOnce());
});

it("во время записи блокирует повторное применение и закрытие", async () => {
    let finish!: (value: Project) => void;
    const post = vi.spyOn(api, "postForm").mockImplementation(() => new Promise<Project>(resolve => { finish = resolve; }));
    const { user, changed } = setup({ project: savedProject });
    const dialog = await open(user);
    await user.upload(within(dialog).getByLabelText("Файл картинки проекта"), new File(["image"], "logo.png", { type: "image/png" }));
    await user.click(within(dialog).getByRole("button", { name: "Применить" }));
    await user.keyboard("{Escape}");
    await user.click(within(dialog).getByRole("button", { name: "Закрыть" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(post).toHaveBeenCalledOnce();
    expect((within(dialog).getByRole("button", { name: "Отмена" }) as HTMLButtonElement).disabled).toBe(true);
    finish(savedProject);
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
});
