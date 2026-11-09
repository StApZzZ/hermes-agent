import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { Eye } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { errorMessage } from "@/lib/api-error";
import { visionApi, type ImageInputMode, type VisionSettings } from "@/lib/api-vision";
import { cn } from "@/lib/utils";
import { useVisionStrings } from "./useVisionStrings";

interface VisionModeCardProps {
  onToast: (message: string, type: "error" | "success") => void;
}

function routeLabel(route: { provider: string; model: string }): string {
  return route.model && route.model !== "default" ? `${route.provider} · ${route.model}` : route.provider;
}

export function VisionModeCard({ onToast }: VisionModeCardProps) {
  const v = useVisionStrings();
  const [settings, setSettings] = useState<VisionSettings | null>(null);
  const [saving, setSaving] = useState<ImageInputMode | null>(null);

  const load = useCallback(() => {
    visionApi.getSettings().then(setSettings).catch((e) => onToast(errorMessage(e), "error"));
  }, [onToast]);

  useEffect(load, [load]);

  const choose = async (mode: ImageInputMode) => {
    if (!settings || mode === settings.image_input_mode) return;
    setSaving(mode);
    try {
      setSettings(await visionApi.setImageInputMode(mode));
      onToast(`${v.saved}: ${v.modes[mode].label}`, "success");
    } catch (e) {
      onToast(`${v.saveFailed}: ${errorMessage(e)}`, "error");
    } finally {
      setSaving(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Eye className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-base">{v.modeTitle}</CardTitle>
        </div>
        <p className="text-sm text-muted-foreground">{v.modeHint}</p>
      </CardHeader>
      <CardContent className="grid gap-4">
        {!settings ? (
          <Spinner />
        ) : (
          <>
            <div role="radiogroup" aria-label={v.modeTitle} className="grid gap-2 sm:grid-cols-3">
              {settings.modes.map((mode) => {
                const active = mode === settings.image_input_mode;
                return (
                  <button
                    key={mode}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    disabled={saving !== null}
                    onClick={() => void choose(mode)}
                    className={cn(
                      "flex flex-col gap-1 border p-3 text-left transition-colors",
                      active ? "border-primary bg-primary/10" : "border-border hover:bg-muted/40",
                    )}
                  >
                    <span className="flex items-center gap-2 font-medium">
                      {v.modes[mode].label}
                      {saving === mode && <Spinner className="h-3 w-3" />}
                      {active && <Badge tone="success">✓</Badge>}
                    </span>
                    <span className="text-xs text-muted-foreground">{v.modes[mode].description}</span>
                  </button>
                );
              })}
            </div>
            <dl className="grid gap-1 text-sm sm:grid-cols-[max-content_1fr] sm:gap-x-4">
              <dt className="text-muted-foreground">{v.mainModel}</dt>
              <dd className="font-mono">{routeLabel(settings.main)}</dd>
              <dt className="text-muted-foreground">{v.describer}</dt>
              <dd className={cn("font-mono", !settings.backend && "text-destructive")}>
                {settings.backend ? routeLabel(settings.backend) : v.noDescriber}
              </dd>
              <dt className="text-muted-foreground">{v.routeNow}</dt>
              <dd>{settings.route === "native" ? v.routeNative : v.routeText}</dd>
            </dl>
            <Link to="/models" className="text-sm underline underline-offset-4">
              {v.changeDescriber}
            </Link>
          </>
        )}
      </CardContent>
    </Card>
  );
}
