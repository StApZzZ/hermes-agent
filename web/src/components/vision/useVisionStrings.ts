import { useI18n } from "@/i18n";
import { visionEn, type VisionStrings } from "@/i18n/vision";

/** Images page strings in the UI language; English where a locale has no translation yet. */
export function useVisionStrings(): VisionStrings {
  const { t } = useI18n();
  return t.vision ?? visionEn;
}
