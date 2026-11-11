import { defineTheme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";
export default defineTheme({
  name: "academic",
  extends: neutralTheme,
  typography: {
    scale: { base: 14, ratio: 1.2 },
    body: { family: "Arial", fallbacks: "Tahoma, sans-serif" },
    heading: { family: "Georgia", fallbacks: "Tahoma, serif" },
  },
  radius: { base: 4, multiplier: 1 },
  tokens: {
    "--color-accent": ["#06786D", "#2DD4BF"],
    "--color-on-accent": ["#FFFFFF", "#04211D"],
    "--color-text-primary": ["#172D3B", "#E5EDF4"],
    "--color-text-secondary": ["#536776", "#AFBFCD"],
    "--color-background-body": ["#F4F7F9", "#101C27"],
    "--color-background-surface": ["#FFFFFF", "#172634"],
    "--color-background-card": ["#FFFFFF", "#172634"],
    "--color-background-muted": ["#EDF2F5", "#12212E"],
    "--color-border": ["#D3DEE5", "#405260"],
  },
  components: {
    button: {
      "variant:primary": {
        backgroundColor: "var(--color-accent)",
        color: "var(--color-on-accent)",
      },
    },
    heading: { base: { letterSpacing: "normal" } },
  },
});
