import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { FileList } from "../components/Files";
import { TeaCup, Wordmark, firstName } from "../brand";
import { pastedFiles, relativeTime } from "../lib";
import type { AttachmentInfo } from "../types";
import { Empty, ErrorState, Field, Spinner } from "../ui";
import { portal, PortalError, portalFile, portalUpload, type Profile, type Request, type RequestDetail } from "./api";

const ACCEPT = "image/png,image/jpeg,image/gif,application/pdf,text/plain,.log,.txt";

// What a status means to the person waiting, not to the desk.
const STATUS: Record<Request["status"], string> = {
  new: "Received",
  open: "Being worked on",
  pending: "Waiting for you",
  resolved: "Resolved",
  closed: "Closed",
};

const SCORES = [
  { score: 1, label: "Poor" },
  { score: 2, label: "Not great" },
  { score: 3, label: "OK" },
  { score: 4, label: "Good" },
  { score: 5, label: "Excellent" },
];

type View = { name: "list" } | { name: "new" } | { name: "request"; id: number };

function parseView(hash: string): View {
  const match = /^#\/r\/(\d{1,12})$/.exec(hash);
  if (match) return { name: "request", id: Number(match[1]) };
  if (hash === "#/new") return { name: "new" };
  return { name: "list" };
}

function RequestStatus({ status }: { status: Request["status"] }) {
  return (
    <span className={`badge status-${status}`}>
      <span className="badge-dot" aria-hidden="true" />
      {STATUS[status]}
    </span>
  );
}

function SignIn() {
  const [sent, setSent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const email = String(new FormData(event.currentTarget).get("email") ?? "").trim();
    setBusy(true);
    setError(null);
    try {
      const result = await portal<{ detail: string }>("POST", "/link", { email });
      setSent(result.detail);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="portal-card card">
      <h1>Follow your requests</h1>
      {sent ? (
        <>
          <p>{sent}</p>
          <p className="muted small">The link works once, for a short while. Check your spam folder if nothing arrives.</p>
        </>
      ) : (
        <form className="form" onSubmit={(e) => void submit(e)}>
          <p className="muted">
            Enter the e-mail address you used to contact the service desk. We will send you a link to sign in — no password needed.
          </p>
          <Field label="Your e-mail address">
            <input name="email" type="email" required maxLength={320} autoComplete="email" />
          </Field>
          {error && <p className="danger-banner">{error}</p>}
          <div>
            <button className="btn btn-primary" disabled={busy}>
              Send me a link
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function RequestList({ onOpen }: { onOpen: (id: number) => void }) {
  const [items, setItems] = useState<Request[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    portal<Request[]>("GET", "/tickets").then(setItems, (e: unknown) => setError(e instanceof Error ? e.message : "Could not load"));
  }, []);
  useEffect(load, [load]);
  if (error) return <ErrorState message={error} retry={load} />;
  if (!items) return <Spinner />;
  if (items.length === 0) return <Empty art={<TeaCup />} title="No requests yet" hint="When you contact the service desk, your requests appear here." />;
  return (
    <ol className="portal-list">
      {items.map((r) => (
        <li key={r.id}>
          <a
            className="card portal-item"
            href={`#/r/${r.id}`}
            onClick={(e) => {
              e.preventDefault();
              onOpen(r.id);
            }}
          >
            <span className="ticket-key">{r.key}</span>
            <strong>{r.subject}</strong>
            <RequestStatus status={r.status} />
            <span className="muted small">
              updated {relativeTime(r.updated_at)}
              {r.can_rate ? " · tell us how we did" : ""}
            </span>
          </a>
        </li>
      ))}
    </ol>
  );
}

function Rate({ request, onRated }: { request: Request; onRated: () => void }) {
  const [score, setScore] = useState<number | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function submit() {
    if (!score) return;
    try {
      await portal("POST", `/tickets/${request.id}/rating`, { score, comment: comment.trim() || null });
      onRated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save your rating");
    }
  }
  return (
    <section className="card portal-rate">
      <h2>How did we do?</h2>
      <div className="rate-scale" role="radiogroup" aria-label="Your rating">
        {SCORES.map((s) => (
          <button key={s.score} role="radio" aria-checked={score === s.score} className={`rate-option${score === s.score ? " rate-chosen" : ""}`} onClick={() => setScore(s.score)}>
            <strong>{s.score}</strong>
            <span>{s.label}</span>
          </button>
        ))}
      </div>
      <Field label="Anything you want to add? (optional)">
        <textarea rows={2} maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} />
      </Field>
      {error && <p className="danger-banner">{error}</p>}
      <div>
        <button className="btn btn-primary" disabled={!score} onClick={() => void submit()}>
          Send rating
        </button>
      </div>
    </section>
  );
}

function RequestView({ id }: { id: number }) {
  const [detail, setDetail] = useState<RequestDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const fetchFile = useCallback((file: AttachmentInfo) => portalFile(id, file.id), [id]);
  const load = useCallback(() => {
    portal<RequestDetail>("GET", `/tickets/${id}`).then(setDetail, (e: unknown) =>
      setError(e instanceof PortalError && e.status === 404 ? "We could not find this request." : "Could not load the request."),
    );
  }, [id]);
  useEffect(load, [load]);

  async function sendFiles(files: File[]) {
    if (files.length === 0) return;
    setBusy(true);
    try {
      for (const file of files) await portalUpload(id, file);
      setNotice("File sent. It can be opened once it has been checked for malware.");
      load();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not send the file");
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setBusy(true);
    try {
      await portal("POST", `/tickets/${id}/messages`, { body: reply });
      setReply("");
      setNotice("Sent. The service desk has been told.");
      load();
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "Could not send");
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorState message={error} />;
  if (!detail) return <Spinner />;
  const t = detail.ticket;
  return (
    <article className="stack">
      <a className="back" href="#/">
        ← Your requests
      </a>
      <header className="ticket-head">
        <span className="ticket-key">{t.key}</span>
        <h1>{t.subject}</h1>
        <div className="ticket-meta">
          <RequestStatus status={t.status} />
          <span className="muted">opened {relativeTime(t.created_at)}</span>
        </div>
      </header>
      {t.merged_into_key && <p className="info-banner">This request continues as {t.merged_into_key}.</p>}
      {t.status === "pending" && <p className="info-banner">The service desk is waiting for your answer.</p>}
      <ol className="thread">
        {detail.messages.map((m, index) => (
          <li key={index} className={`msg ${m.from_you ? "msg-inbound" : "msg-outbound"}`}>
            <div className="msg-bubble">
              <header>
                <strong>{m.author}</strong>
                <time dateTime={m.created_at} title={new Date(m.created_at).toLocaleString()}>
                  {relativeTime(m.created_at)}
                </time>
              </header>
              <p className="msg-body">{m.body}</p>
              <FileList files={m.attachments} fetchFile={fetchFile} canOpen />
            </div>
          </li>
        ))}
      </ol>
      {t.can_rate && <Rate request={t} onRated={load} />}
      {t.rating && <p className="success-banner">Thank you — you rated this {t.rating} out of 5.</p>}
      {t.can_reply ? (
        <div className="composer composer-outbound">
          <textarea
            rows={4}
            maxLength={100000}
            value={reply}
            aria-label="Your message"
            placeholder="Add information or answer a question… You can paste a screenshot here."
            onChange={(e) => setReply(e.target.value)}
            onPaste={(e) => {
              const files = pastedFiles([...e.clipboardData.items]);
              if (files.length > 0) {
                e.preventDefault();
                void sendFiles(files);
              }
            }}
          />
          <div className="composer-foot">
            <input
              ref={fileInput}
              type="file"
              hidden
              multiple
              accept={ACCEPT}
              onChange={(e) => {
                void sendFiles([...(e.target.files ?? [])]);
                e.target.value = "";
              }}
            />
            <button className="btn btn-quiet" disabled={busy} onClick={() => fileInput.current?.click()}>
              Add a file
            </button>
            <span className="muted">{notice ?? (t.status === "resolved" ? "Writing again reopens the request." : "")}</span>
            <button className="btn btn-primary" disabled={busy || !reply.trim()} onClick={() => void send()}>
              Send
            </button>
          </div>
        </div>
      ) : (
        <p className="muted">This request is closed. If you need more help, start a new request.</p>
      )}
    </article>
  );
}

function NewRequest({ onCreated }: { onCreated: (id: number) => void }) {
  const [systems, setSystems] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    portal<{ id: string; name: string }[]>("GET", "/systems").then(setSystems, () => setSystems([]));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const created = await portal<Request>("POST", "/tickets", {
        subject: String(data.get("subject") ?? "").trim(),
        body: String(data.get("body") ?? ""),
        system_id: String(data.get("system") ?? "") || null,
      });
      for (const file of data.getAll("files")) {
        if (file instanceof File && file.size > 0) await portalUpload(created.id, file).catch(() => undefined);
      }
      onCreated(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the request");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="portal-card card">
      <a className="back" href="#/">
        ← Your requests
      </a>
      <h1>New request</h1>
      <form className="form" onSubmit={(e) => void submit(e)}>
        <Field label="What is it about?">
          <input name="subject" required maxLength={200} />
        </Field>
        {systems.length > 0 && (
          <Field label="Which system? (if you know)">
            <select name="system" defaultValue="">
              <option value="">I'm not sure</option>
              {systems.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Describe the problem">
          <textarea name="body" required rows={6} maxLength={100000} />
        </Field>
        <Field label="Screenshots or files (optional)" hint="Images, PDF or text files. Each is checked for malware before the service desk opens it.">
          <input name="files" type="file" multiple accept={ACCEPT} />
        </Field>
        {error && <p className="danger-banner">{error}</p>}
        <div>
          <button className="btn btn-primary" disabled={busy}>
            Send request
          </button>
        </div>
      </form>
    </section>
  );
}

export function Portal() {
  const [profile, setProfile] = useState<Profile | null | undefined>(undefined);
  const [failure, setFailure] = useState<string | null>(null);
  const [view, setView] = useState<View>(() => parseView(window.location.hash));

  const start = useCallback(async () => {
    const token = /^#token=([\w-]{20,200})$/.exec(window.location.hash)?.[1];
    if (token) {
      // Take the one-time link out of the address bar and history at once.
      window.history.replaceState(null, "", "/portal#/");
      setView({ name: "list" });
      try {
        setProfile(await portal<Profile>("POST", "/session", { token }));
        setFailure(null);
        return;
      } catch {
        setFailure("This sign-in link has expired or was already used. Ask for a new one below.");
      }
    }
    try {
      setProfile(await portal<Profile>("GET", "/me"));
    } catch {
      setProfile(null);
    }
  }, []);

  useEffect(() => {
    void start();
    const onHash = () => {
      if (window.location.hash.startsWith("#token=")) void start();
      else setView(parseView(window.location.hash));
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [start]);

  const go = (hash: string) => {
    window.location.hash = hash;
  };

  async function signOut() {
    await portal("POST", "/sign-out").catch(() => undefined);
    setProfile(null);
    go("#/");
  }

  return (
    <div className="portal">
      <header className="portal-bar">
        <a href="#/" className="portal-brand" aria-label="Your requests">
          <Wordmark />
          <span>Service desk</span>
        </a>
        {profile && (
          <div className="portal-who">
            <span className="muted">{profile.display_name ? firstName(profile.display_name) : profile.email}</span>
            <button className="btn btn-quiet" onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        )}
      </header>
      <main className="portal-main" id="main">
        {profile === undefined ? (
          <Spinner label="Signing in" />
        ) : profile === null ? (
          <>
            {failure && <p className="warning-banner portal-card">{failure}</p>}
            <SignIn />
          </>
        ) : view.name === "request" ? (
          <RequestView key={view.id} id={view.id} />
        ) : view.name === "new" ? (
          <NewRequest onCreated={(id) => go(`#/r/${id}`)} />
        ) : (
          <section className="stack">
            <div className="page-head">
              <div>
                <h1>Your requests</h1>
                <p>Everything you have asked the service desk, and where it stands.</p>
              </div>
              <a className="btn btn-primary" href="#/new">
                New request
              </a>
            </div>
            <RequestList onOpen={(id) => go(`#/r/${id}`)} />
          </section>
        )}
      </main>
      <footer className="portal-foot muted small">
        Only you can see your requests. Internal notes by the service desk are not shown here.
      </footer>
    </div>
  );
}
