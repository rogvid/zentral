import { useConfirm } from "../../components/Confirm";
import { useApp } from "../../context";

/** Run an admin action with uniform success/error feedback. */
export function useAction() {
  const { notify } = useApp();
  return async (work: () => Promise<unknown>, done?: string): Promise<boolean> => {
    try {
      await work();
      if (done) notify(done);
      return true;
    } catch (e) {
      notify(e instanceof Error ? e.message : "Action failed", "error");
      return false;
    }
  };
}

/** Ask before deleting, then delete. */
export function useDelete() {
  const confirm = useConfirm();
  const run = useAction();
  return async (what: string, consequence: string, work: () => Promise<unknown>) => {
    const ok = await confirm({ title: `Delete ${what}?`, message: consequence, confirmLabel: "Delete", danger: true });
    if (ok) await run(work, `Deleted ${what}`);
  };
}
