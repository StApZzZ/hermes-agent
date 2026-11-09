import { installCommand, type VisionAction, type VisionActionResult } from "@/lib/api-vision";
import type { VisionStrings } from "@/i18n/vision";

interface VisionResultsProps {
  results: Partial<Record<VisionAction, VisionActionResult>>;
  v: VisionStrings;
}

function ActionBody({ action, result, v }: { action: VisionAction; result: VisionActionResult; v: VisionStrings }) {
  if (result.missing) {
    const command = installCommand(result.install);
    return (
      <p className="text-sm text-muted-foreground">
        {result.missing}: {v.notInstalled}
        {command && <code className="ml-2 bg-muted px-2 py-0.5 text-xs">{command}</code>}
      </p>
    );
  }
  if (result.error) return <p className="text-sm text-destructive">{result.error}</p>;
  if (action === "codes") {
    if (!result.codes?.length) return <p className="text-sm text-muted-foreground">{v.noCodes}</p>;
    return (
      <ul className="grid gap-1 text-sm">
        {result.codes.map((code, i) => (
          <li key={i} className="break-all">
            <span className="text-muted-foreground">{code.type}: </span>
            <span className="font-mono">{code.data}</span>
          </li>
        ))}
      </ul>
    );
  }
  if (action === "meta") {
    const entries = Object.entries(result.meta ?? {});
    if (!entries.length) return <p className="text-sm text-muted-foreground">{v.noExif}</p>;
    return (
      <div className="grid gap-2 text-sm">
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
          {entries.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-muted-foreground">{key}</dt>
              <dd className="font-mono">{String(value)}</dd>
            </div>
          ))}
        </dl>
        {result.gps && (
          <a href={result.gps.map} target="_blank" rel="noreferrer" className="underline underline-offset-4">
            {v.openMap}
          </a>
        )}
      </div>
    );
  }
  const text = (result.text ?? "").trim();
  if (!text) return <p className="text-sm text-muted-foreground">{v.noText}</p>;
  return <pre className="whitespace-pre-wrap break-words bg-muted/40 p-3 text-sm">{text}</pre>;
}

export function VisionResults({ results, v }: VisionResultsProps) {
  return (
    <div className="grid gap-4">
      {(Object.entries(results) as [VisionAction, VisionActionResult][]).map(([action, result]) => (
        <section key={action} className="grid gap-2">
          <h3 className="text-sm font-medium">{v.actions[action]}</h3>
          <ActionBody action={action} result={result} v={v} />
        </section>
      ))}
    </div>
  );
}
