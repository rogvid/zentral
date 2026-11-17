// Zentral's visual identity: the mark and a few small line illustrations.
//
// The mark is a Z whose diagonal passes through a single warm dot — the
// centre that everything arrives at. The illustrations are deliberately quiet
// line drawings in the ink colour with one saffron accent, used where the
// application would otherwise have nothing to say.

export function Mark({ size = 30 }: { size?: number }) {
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="9" className="mark-tile" />
      <path d="M10 10.5h12L10 21.5h12" className="mark-z" />
      <circle cx="16" cy="16" r="2.7" className="mark-dot" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="wordmark">
      <Mark />
      <span>Zentral</span>
    </span>
  );
}

/** Nothing is waiting. */
export function TeaCup() {
  return (
    <svg className="art" viewBox="0 0 120 96" aria-hidden="true" focusable="false">
      <g className="art-steam">
        <path d="M50 30c-4-5 4-8 0-14" />
        <path d="M62 30c-4-5 4-8 0-14" />
        <path d="M74 30c-4-5 4-8 0-14" />
      </g>
      <path className="art-line" d="M36 40h52v14a26 26 0 0 1-52 0z" />
      <path className="art-line" d="M88 46h4a8 8 0 0 1 0 16h-6" />
      <path className="art-line" d="M26 84h72" />
      <circle className="art-accent" cx="62" cy="54" r="3" />
    </svg>
  );
}

/** A search or filter found nothing. */
export function Compass() {
  return (
    <svg className="art" viewBox="0 0 120 96" aria-hidden="true" focusable="false">
      <circle className="art-line" cx="60" cy="48" r="30" />
      <circle className="art-line art-faint" cx="60" cy="48" r="22" />
      <path className="art-line" d="M60 14v6M60 76v6M26 48h6M88 48h6" />
      <path className="art-fill" d="m60 48 9-17-9 4-9-4z" />
      <path className="art-line" d="m60 48-9 17 9-4 9 4z" />
      <circle className="art-accent" cx="60" cy="48" r="3" />
    </svg>
  );
}

/** A list that is empty because nobody has added anything yet. */
export function Seedling() {
  return (
    <svg className="art" viewBox="0 0 120 96" aria-hidden="true" focusable="false">
      <path className="art-line" d="M60 80V46" />
      <path className="art-line" d="M60 56c-14 0-22-8-22-20 12 0 22 6 22 20z" />
      <path className="art-line" d="M60 48c0-14 8-22 22-22 0 14-8 22-22 22z" />
      <path className="art-line" d="M34 82h52" />
      <circle className="art-accent" cx="82" cy="26" r="3" />
    </svg>
  );
}

/** Something broke. */
export function Kite() {
  return (
    <svg className="art" viewBox="0 0 120 96" aria-hidden="true" focusable="false">
      <path className="art-line" d="M60 12 82 40 60 64 38 40z" />
      <path className="art-line art-faint" d="M60 12v52M38 40h44" />
      <path className="art-line" d="M60 64c-6 8 6 10 0 18" />
      <circle className="art-accent" cx="60" cy="40" r="3" />
    </svg>
  );
}

/** "Good morning", "Good afternoon", "Good evening" — by the viewer's clock. */
export function greeting(now: Date = new Date()): string {
  const hour = now.getHours();
  if (hour < 5) return "Working late";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

export function firstName(displayName: string): string {
  const name = displayName.trim().split(/\s+/)[0] ?? "";
  return name.includes("@") ? "" : name;
}
