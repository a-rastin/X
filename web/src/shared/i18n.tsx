import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from "react";
import { message, present, presentError, type Language, type MessageKey } from "./messages";

const LanguageContext = createContext<{ language: Language; setLanguage: (language: Language) => void } | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>("en");
  useLayoutEffect(() => {
    document.documentElement.lang = language;
    document.documentElement.dir = language === "fa" ? "rtl" : "ltr";
  }, [language]);
  return <LanguageContext.Provider value={{ language, setLanguage }}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (context === null) throw new Error("LanguageProvider is required");
  return {
    ...context,
    t: (key: MessageKey, values?: Record<string, string | number>) => message(context.language, key, values),
    text: (source: string) => present(context.language, source),
    errorText: (source: string | null) => source === null ? "" : presentError(context.language, source),
  };
}

export function LanguageButton() {
  const { language, setLanguage, t } = useLanguage();
  return (
    <button
      type="button"
      className="xi-btn xi-btn-secondary xi-language-button"
      aria-label={t(language === "en" ? "switchToPersian" : "switchToEnglish")}
      onClick={() => setLanguage(language === "en" ? "fa" : "en")}
      lang={language === "en" ? "fa" : "en"}
      dir={language === "en" ? "rtl" : "ltr"}
    >
      {language === "en" ? "فارسی" : "English"}
    </button>
  );
}
