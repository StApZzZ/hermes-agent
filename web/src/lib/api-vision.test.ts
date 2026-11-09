import { describe, expect, it } from "vitest";
import { installCommand } from "./api-vision";

describe("installCommand", () => {
  it("prefers apt on the Linux server, then npm with its login, then brew", () => {
    expect(installCommand({ apt: "zbar-tools", brew: "zbar" })).toBe("sudo apt install zbar-tools");
    expect(installCommand({ npm: "@openai/codex", login: "codex login --device-auth" })).toBe(
      "npm install -g @openai/codex && codex login --device-auth",
    );
    expect(installCommand({ brew: "exiftool", winget: null })).toBe("brew install exiftool");
  });

  it("shows nothing when there is no hint", () => {
    expect(installCommand(undefined)).toBeNull();
    expect(installCommand({ winget: null })).toBeNull();
  });
});
