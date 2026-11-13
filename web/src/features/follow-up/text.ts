import { en, fa } from "../../shared/messages";
import { useLanguage } from "../../shared/i18n";
import { followUpMessage } from "./labels";
export function useFollowUpText() {
  const { language } = useLanguage();
  return (key: string) =>
    followUpMessage(language, key, language === "fa" ? fa : en);
}
