// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { UserSummary } from "@/lib/types";
import { UserAvatar } from "./UserAvatar";
import { UserIdentity } from "./UserIdentity";

const user: UserSummary = { id: 7, username: "ivan", first_name: "Иван", last_name: "Петров", middle_name: null, has_avatar: true };
afterEach(cleanup);

it("выбирает фотографию каждого пользователя по его ID, сохраняя доступное имя", () => {
    const { container } = render(<><UserIdentity user={user} /><UserIdentity user={{ ...user, id: 8, first_name: "Анна" }} /></>);
    const paths = Array.from(container.querySelectorAll("img")).map(image => new URL(image.src).pathname);
    expect(paths).toEqual(["/api/v1/users/7/avatar", "/api/v1/users/8/avatar"]);
    expect(screen.getByText("Петров Иван")).toBeTruthy();
    expect(screen.getByText("Петров Анна")).toBeTruthy();
    expect(container.querySelectorAll('img[src*="/me/"]')).toHaveLength(0);
});

it("не загружает фото для отсутствующего аватара или snapshot без ID", () => {
    const { container } = render(<><UserIdentity user={{ ...user, has_avatar: false }} /><UserIdentity name="Сидоров Алексей" /></>);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("ПИ")).toBeTruthy();
    expect(screen.getByText("СА")).toBeTruthy();
});

it("может показать автора по ID без полного профиля и сохраняет имя при ошибке картинки", () => {
    const { container } = render(<UserIdentity userId={42} name="Иван Петров" />);
    const image = container.querySelector("img")!;
    expect(new URL(image.src).pathname).toBe("/api/v1/users/42/avatar");
    fireEvent.error(image);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("ИП")).toBeTruthy();
    expect(screen.getByText("Иван Петров")).toBeTruthy();
});

it("без корректного ID не подставляет фотографию текущего пользователя", () => {
    const { container } = render(<><UserAvatar hasAvatar /><UserAvatar userId={0} hasAvatar /><UserAvatar userId={-1} hasAvatar /></>);
    expect(container.querySelector("img")).toBeNull();
});

it("новая версия аватара повторяет загрузку после прежней ошибки", () => {
    const { container, rerender } = render(<UserAvatar user={user} version={1} />);
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector("img")).toBeNull();
    rerender(<UserAvatar user={user} version={2} />);
    expect(new URL(container.querySelector("img")!.src).searchParams.get("v")).toBe("2");
});
