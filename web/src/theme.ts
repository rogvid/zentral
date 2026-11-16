import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "zentral.theme";
const ORDER: ThemeChoice[] = ["system", "light", "dark"];

export function nextTheme(current: ThemeChoice): ThemeChoice {
  return ORDER[(ORDER.indexOf(current) + 1) % ORDER.length] ?? "system";
}

function stored(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function apply(choice: ThemeChoice): void {
  if (choice === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = choice;
  try {
    if (choice === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    /* preference simply will not persist */
  }
}

export function useTheme(): [ThemeChoice, () => void] {
  const [choice, setChoice] = useState<ThemeChoice>(stored);
  useEffect(() => apply(choice), [choice]);
  const cycle = useCallback(() => setChoice(nextTheme), []);
  return [choice, cycle];
}
