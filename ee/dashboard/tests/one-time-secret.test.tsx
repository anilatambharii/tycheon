// @vitest-environment jsdom
// Proprietary: see ee/LICENSE
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import OneTimeSecret from "@/components/OneTimeSecret";

afterEach(() => cleanup());

function Harness({ secret }: { secret: string }) {
  const [s, setS] = useState<string | null>(secret);
  return <div>{s ? <OneTimeSecret label="API key" secret={s} onDismiss={() => setS(null)} /> : <p>gone</p>}</div>;
}

describe("OneTimeSecret", () => {
  it("shows the secret with a once-only warning", () => {
    render(<OneTimeSecret label="API key" secret="tyk_abc123" onDismiss={() => undefined} />);
    expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe("tyk_abc123");
    expect(screen.getByRole("alert").textContent).toMatch(/only once/i);
  });

  it("copies to the clipboard and announces it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<OneTimeSecret label="API key" secret="tyk_abc123" onDismiss={() => undefined} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    });
    expect(writeText).toHaveBeenCalledWith("tyk_abc123");
    expect(screen.getByText("Copied to clipboard.")).toBeTruthy();
  });

  it("reports a copy failure", async () => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockRejectedValue(new Error("no")) }, configurable: true });
    render(<OneTimeSecret label="API key" secret="s" onDismiss={() => undefined} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    });
    expect(screen.getByText(/Copy failed/)).toBeTruthy();
  });

  it("removes the secret from the DOM once dismissed", () => {
    render(<Harness secret="tyk_abc123" />);
    expect(screen.queryByDisplayValue("tyk_abc123")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "I have saved it" }));
    expect(screen.queryByDisplayValue("tyk_abc123")).toBeNull();
    expect(screen.getByText("gone")).toBeTruthy();
  });
});
