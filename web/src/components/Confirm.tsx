import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { Dialog } from "../ui";

interface ConfirmOptions {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
}

type Ask = (options: ConfirmOptions) => Promise<boolean>;
const ConfirmContext = createContext<Ask | null>(null);

/** `const confirm = useConfirm(); if (await confirm({...})) …` — for anything destructive. */
export function useConfirm(): Ask {
  const ask = useContext(ConfirmContext);
  if (!ask) throw new Error("ConfirmProvider is missing");
  return ask;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((answer: boolean) => void) | null>(null);

  const ask = useCallback<Ask>((next) => {
    setOptions(next);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  function answer(value: boolean) {
    resolver.current?.(value);
    resolver.current = null;
    setOptions(null);
  }

  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      {options && (
        <Dialog title={options.title} onClose={() => answer(false)}>
          <div className="form">
            <div>{options.message}</div>
            <footer className="form-actions">
              <button className="btn" autoFocus onClick={() => answer(false)}>
                Cancel
              </button>
              <button className={`btn ${options.danger ? "btn-danger-solid" : "btn-primary"}`} onClick={() => answer(true)}>
                {options.confirmLabel ?? "Confirm"}
              </button>
            </footer>
          </div>
        </Dialog>
      )}
    </ConfirmContext.Provider>
  );
}
