// Images page wire types and calls (/api/vision/*). Kept out of api.ts so that facade stays under
// its code-health line cap. Every call names the managed profile explicitly.
import { fetchJSON, getManagementProfile } from "@/lib/api";

export type ImageInputMode = "auto" | "native" | "text";
export type VisionAction = "describe" | "ocr" | "codes" | "meta";

export interface VisionRoute {
  provider: string;
  model: string;
}

export interface VisionSettings {
  image_input_mode: ImageInputMode;
  modes: ImageInputMode[];
  main: VisionRoute;
  aux: VisionRoute;
  /** How an image sent now reaches the main model: pixels or a vision_analyze description. */
  route: "native" | "text";
  /** The backend vision_analyze resolves to right now; null when none is reachable. */
  backend: VisionRoute | null;
  backend_error: string | null;
}

export interface InstallHint {
  apt?: string;
  brew?: string;
  winget?: string | null;
  npm?: string;
  login?: string;
}

export interface VisionToolStatus {
  installed: boolean;
  path: string | null;
  install?: InstallHint;
  langs?: string[];
}

export interface VisionToolsReport {
  tools?: Record<string, VisionToolStatus>;
  pillow?: boolean;
  error?: string;
}

/** One action's answer: the helper's JSON, ``{missing, install}`` or ``{error}``. */
export interface VisionActionResult {
  error?: string;
  missing?: string;
  install?: InstallHint;
  text?: string;
  langs?: string;
  codes?: Array<{ type: string; data: string }>;
  meta?: Record<string, string | number>;
  gps?: { lat: number; lon: number; map: string } | null;
  has_exif?: boolean;
}

export interface VisionAnalyzeResponse {
  path: string;
  results: Partial<Record<VisionAction, VisionActionResult>>;
}

export type PictureAspect = "landscape" | "square" | "portrait";

export interface GenerationChoice {
  name: string;
  label: string;
}

export interface GenerationState {
  /** image_gen.provider; null when unset. */
  provider: string | null;
  /** Backends available on this host. */
  choices: GenerationChoice[];
}

export interface GenerateResponse {
  success: boolean;
  provider?: string | null;
  /** The picture as a data URL (or a remote URL). */
  image?: string;
  path?: string;
  error?: string | null;
}

function scoped(url: string): string {
  const profile = getManagementProfile();
  if (!profile) return url;
  return `${url}${url.includes("?") ? "&" : "?"}profile=${encodeURIComponent(profile)}`;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export const visionApi = {
  getSettings: () => fetchJSON<VisionSettings>(scoped("/api/vision/settings")),
  setImageInputMode: (mode: ImageInputMode) =>
    fetchJSON<VisionSettings>(scoped("/api/vision/settings"), {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify({ image_input_mode: mode }),
    }),
  getTools: () => fetchJSON<VisionToolsReport>(scoped("/api/vision/tools")),
  getGeneration: () => fetchJSON<GenerationState>(scoped("/api/vision/generation")),
  setGenerationProvider: (provider: string) =>
    fetchJSON<GenerationState>(scoped("/api/vision/generation"), {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify({ provider }),
    }),
  generate: (prompt: string, aspectRatio: PictureAspect) =>
    fetchJSON<GenerateResponse>(scoped("/api/vision/generate"), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ prompt, aspect_ratio: aspectRatio }),
    }),
  analyze: (path: string, actions: VisionAction[], prompt: string, lang: string) =>
    fetchJSON<VisionAnalyzeResponse>(scoped("/api/vision/analyze"), {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ path, actions, prompt, lang }),
    }),
};

/** The one command to show for a missing tool on a Linux server (the fork's default host). */
export function installCommand(hint: InstallHint | undefined): string | null {
  if (!hint) return null;
  if (hint.apt) return `sudo apt install ${hint.apt}`;
  if (hint.npm) return `npm install -g ${hint.npm}${hint.login ? ` && ${hint.login}` : ""}`;
  if (hint.brew) return `brew install ${hint.brew}`;
  return null;
}
