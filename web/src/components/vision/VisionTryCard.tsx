import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { ImageUp, ScanSearch } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { getManagementProfile } from "@/lib/api";
import { errorMessage } from "@/lib/api-error";
import { visionApi, type VisionAction, type VisionAnalyzeResponse } from "@/lib/api-vision";
import { firstImageFromClipboard, uploadChatImage } from "@/lib/chatImagePaste";
import { cn } from "@/lib/utils";
import { useVisionStrings } from "./useVisionStrings";
import { VisionResults } from "./VisionResults";

const ACTIONS: VisionAction[] = ["describe", "ocr", "codes", "meta"];

interface VisionTryCardProps {
  onToast: (message: string, type: "error" | "success") => void;
}

export function VisionTryCard({ onToast }: VisionTryCardProps) {
  const v = useVisionStrings();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [lang, setLang] = useState("rus+eng");
  const [actions, setActions] = useState<Set<VisionAction>>(new Set(["describe", "ocr"]));
  const [running, setRunning] = useState(false);
  const [response, setResponse] = useState<VisionAnalyzeResponse | null>(null);

  const preview = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);
  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  const pick = (image: File | null) => {
    setFile(image);
    setResponse(null);
  };

  // A pasted screenshot is the fastest way in; only while this page is open.
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const image = firstImageFromClipboard(event.clipboardData);
      if (image) {
        event.preventDefault();
        pick(image);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, []);

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const image = firstImageFromClipboard(event.dataTransfer);
    if (image) pick(image);
  };

  const toggle = (action: VisionAction) =>
    setActions((current) => {
      const next = new Set(current);
      if (next.has(action)) next.delete(action);
      else next.add(action);
      return next;
    });

  const run = async () => {
    if (!file || actions.size === 0) return;
    setRunning(true);
    try {
      const uploaded = await uploadChatImage(file, getManagementProfile());
      setResponse(await visionApi.analyze(uploaded.path, ACTIONS.filter((a) => actions.has(a)), prompt.trim() || v.defaultQuestion, lang));
    } catch (e) {
      onToast(`${v.uploadFailed}: ${errorMessage(e)}`, "error");
    } finally {
      setRunning(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ScanSearch className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-base">{v.tryTitle}</CardTitle>
        </div>
        <p className="text-sm text-muted-foreground">{v.tryHint}</p>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cn(
            "flex min-h-40 flex-col items-center justify-center gap-3 border border-dashed p-4 text-center",
            dragging ? "border-primary bg-primary/10" : "border-border",
          )}
        >
          {preview ? (
            <img src={preview} alt="" className="max-h-64 max-w-full object-contain" />
          ) : (
            <ImageUp className="h-8 w-8 text-muted-foreground" />
          )}
          <span className="text-sm text-muted-foreground">{v.dropHere}</span>
          <Button outlined size="sm" onClick={() => fileInput.current?.click()}>
            {v.pickFile}
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp,image/bmp"
            className="hidden"
            onChange={(event) => pick(event.target.files?.[0] ?? null)}
          />
        </div>

        <div className="flex flex-wrap gap-4">
          {ACTIONS.map((action) => (
            <label key={action} className="flex items-center gap-2 text-sm">
              <Checkbox checked={actions.has(action)} onCheckedChange={() => toggle(action)} />
              {v.actions[action]}
            </label>
          ))}
        </div>

        <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
          <label className="grid gap-1 text-sm">
            {v.question}
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder={v.questionPlaceholder}
              rows={2}
              className="border border-border bg-background p-2"
            />
          </label>
          <label className="grid gap-1 text-sm">
            {v.ocrLanguages}
            <input
              value={lang}
              onChange={(event) => setLang(event.target.value)}
              className="border border-border bg-background p-2 font-mono"
            />
          </label>
        </div>

        <div className="flex items-center gap-3">
          <Button size="sm" onClick={() => void run()} disabled={!file || running || actions.size === 0}>
            {running ? v.running : v.run}
          </Button>
          {running && <Spinner className="h-4 w-4" />}
        </div>

        {response && <VisionResults results={response.results} v={v} />}
        <p className="text-xs text-muted-foreground">{v.chatHint}</p>
      </CardContent>
    </Card>
  );
}
