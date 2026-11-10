import { Toast } from "@nous-research/ui/ui/components/toast";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { VisionDrawCard } from "@/components/vision/VisionDrawCard";
import { VisionModeCard } from "@/components/vision/VisionModeCard";
import { VisionToolsCard } from "@/components/vision/VisionToolsCard";
import { VisionTryCard } from "@/components/vision/VisionTryCard";
import { useVisionStrings } from "@/components/vision/useVisionStrings";

export default function VisionPage() {
  const v = useVisionStrings();
  const { toast, showToast } = useToast();

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">{v.subtitle}</p>
      <VisionModeCard onToast={showToast} />
      <VisionTryCard onToast={showToast} />
      <VisionDrawCard onToast={showToast} />
      <VisionToolsCard />
      <Toast toast={toast} />
    </div>
  );
}
