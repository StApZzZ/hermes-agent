import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Wrench } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { errorMessage } from "@/lib/api-error";
import { installCommand, visionApi, type VisionToolsReport } from "@/lib/api-vision";
import { useVisionStrings } from "./useVisionStrings";

export function VisionToolsCard() {
  const v = useVisionStrings();
  const [report, setReport] = useState<VisionToolsReport | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchTools = useCallback(
    () =>
      visionApi
        .getTools()
        .then(setReport)
        .catch((e) => setReport({ error: errorMessage(e) }))
        .finally(() => setLoading(false)),
    [],
  );

  useEffect(() => {
    void fetchTools();
  }, [fetchTools]);

  const load = () => {
    setLoading(true);
    void fetchTools();
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Wrench className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="flex-1 text-base">{v.toolsTitle}</CardTitle>
          <Button ghost size="sm" onClick={load} disabled={loading} prefix={<RefreshCw className="h-4 w-4" />}>
            {v.refresh}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">{v.toolsHint}</p>
      </CardHeader>
      <CardContent className="grid gap-3">
        {!report && <Spinner />}
        {report?.error && <p className="text-sm text-destructive">{report.error}</p>}
        {Object.entries(report?.tools ?? {}).map(([name, tool]) => {
          const command = tool.installed ? null : installCommand(tool.install);
          return (
            <div key={name} className="grid gap-1 border-b border-border pb-3 last:border-b-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono font-medium">{name}</span>
                <Badge tone={tool.installed ? "success" : "warning"}>
                  {tool.installed ? v.installed : v.missing}
                </Badge>
                <span className="text-sm text-muted-foreground">{v.toolPurpose[name] ?? ""}</span>
              </div>
              {tool.langs && tool.langs.length > 0 && (
                <span className="text-xs text-muted-foreground">
                  {v.languages}: {tool.langs.join(", ")}
                </span>
              )}
              {command && <code className="w-fit bg-muted px-2 py-1 text-xs">{command}</code>}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
