import { useEffect, useState } from "react";
import { Palette } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { errorMessage } from "@/lib/api-error";
import { visionApi, type GenerationState, type PictureAspect } from "@/lib/api-vision";
import { cn } from "@/lib/utils";
import { useVisionStrings } from "./useVisionStrings";

const ASPECTS: PictureAspect[] = ["landscape", "square", "portrait"];

interface VisionDrawCardProps {
  onToast: (message: string, type: "error" | "success") => void;
}

export function VisionDrawCard({ onToast }: VisionDrawCardProps) {
  const v = useVisionStrings();
  const [state, setState] = useState<GenerationState | null>(null);
  const [prompt, setPrompt] = useState("");
  const [aspect, setAspect] = useState<PictureAspect>("landscape");
  const [drawing, setDrawing] = useState(false);
  const [picture, setPicture] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    visionApi
      .getGeneration()
      .then(setState)
      .catch((e) => onToast(errorMessage(e), "error"));
  }, [onToast]);

  const choose = async (provider: string) => {
    try {
      setState(await visionApi.setGenerationProvider(provider));
    } catch (e) {
      onToast(`${v.saveFailed}: ${errorMessage(e)}`, "error");
    }
  };

  const draw = async () => {
    setDrawing(true);
    setError(null);
    try {
      const result = await visionApi.generate(prompt.trim(), aspect);
      if (result.success && result.image) setPicture(result.image);
      else setError(result.error ?? "—");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setDrawing(false);
    }
  };

  const choices = state?.choices ?? [];
  const selected = state?.provider ?? null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Palette className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-base">{v.draw.title}</CardTitle>
        </div>
        <p className="text-sm text-muted-foreground">{v.draw.hint}</p>
      </CardHeader>
      <CardContent className="grid gap-4">
        {!state ? (
          <Spinner />
        ) : choices.length === 0 ? (
          <p className="text-sm text-muted-foreground">{v.draw.noBackend}</p>
        ) : (
          <>
            <div className="grid gap-2">
              <span className="text-sm">{v.draw.backend}</span>
              <div role="radiogroup" aria-label={v.draw.backend} className="flex flex-wrap gap-2">
                {choices.map((choice) => (
                  <button
                    key={choice.name}
                    type="button"
                    role="radio"
                    aria-checked={choice.name === selected}
                    onClick={() => void choose(choice.name)}
                    className={cn(
                      "border px-3 py-1.5 text-sm transition-colors",
                      choice.name === selected ? "border-primary bg-primary/10" : "border-border hover:bg-muted/40",
                    )}
                  >
                    {choice.label}
                  </button>
                ))}
              </div>
            </div>
            <label className="grid gap-1 text-sm">
              {v.draw.prompt}
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder={v.draw.placeholder}
                rows={3}
                className="border border-border bg-background p-2"
              />
            </label>
            <div className="flex flex-wrap items-center gap-2">
              {ASPECTS.map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={value === aspect}
                  onClick={() => setAspect(value)}
                  className={cn(
                    "border px-3 py-1 text-xs",
                    value === aspect ? "border-primary bg-primary/10" : "border-border hover:bg-muted/40",
                  )}
                >
                  {v.draw.aspects[value]}
                </button>
              ))}
              <Button size="sm" onClick={() => void draw()} disabled={drawing || !selected || !prompt.trim()}>
                {drawing ? v.draw.running : v.draw.run}
              </Button>
              {drawing && <Spinner className="h-4 w-4" />}
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            {picture && (
              <div className="grid gap-2">
                <img src={picture} alt={prompt} className="max-h-[32rem] max-w-full object-contain" />
                <a href={picture} download="picture.png" className="w-fit text-sm underline underline-offset-4">
                  {v.draw.download}
                </a>
              </div>
            )}
          </>
        )}
        <p className="text-xs text-muted-foreground">{v.draw.chatHint}</p>
      </CardContent>
    </Card>
  );
}
