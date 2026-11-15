import { useState, type FormEvent } from "react";
import { ApiError } from "../api";
import { useApp } from "../context";
import type { Contact } from "../types";
import { Dialog, Empty, Field, PageHead } from "../ui";

export function PrivacyPage() {
  const { api, can, notify } = useApp();
  const [contact, setContact] = useState<Contact | null>(null);
  const [missing, setMissing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmation, setConfirmation] = useState("");

  async function lookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const email = String(new FormData(event.currentTarget).get("email") ?? "").trim();
    setContact(null);
    setMissing(false);
    try {
      setContact(await api.post<Contact>("/privacy/contacts/lookup", { email }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else notify(e instanceof Error ? e.message : "Lookup failed", "error");
    }
  }

  async function exportData(c: Contact) {
    try {
      const data = await api.get<unknown>(`/privacy/contacts/${c.id}/export`);
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `data-subject-export-${c.id}.json`;
      link.click();
      URL.revokeObjectURL(url);
      notify("Export downloaded. Handle the file as confidential.");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Export failed", "error");
    }
  }

  async function erase(c: Contact) {
    try {
      const result = await api.post<{ tickets_scrubbed: number; messages_scrubbed: number }>(
        `/privacy/contacts/${c.id}/erase`,
      );
      notify(`Erased: ${result.tickets_scrubbed} tickets and ${result.messages_scrubbed} messages scrubbed.`);
      setContact(null);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Erasure failed", "error");
    } finally {
      setConfirming(false);
      setConfirmation("");
    }
  }

  return (
    <>
      <PageHead title="Privacy requests" sub="Handle data-subject access and erasure requests (GDPR Art. 15, 17, 20)." />
      <form className="card panel form" onSubmit={(e) => void lookup(e)}>
        <Field label="Data subject's e-mail address" hint="Every lookup is recorded in the audit trail.">
          <input name="email" type="email" required maxLength={320} autoComplete="off" />
        </Field>
        <div>
          <button className="btn btn-primary">Find person</button>
        </div>
      </form>

      {missing && <Empty title="No data held" hint="No requester with this address exists in Zentral." />}

      {contact && (
        <div className="card panel">
          <h2>{contact.display_name ?? contact.email}</h2>
          <dl className="facts">
            <dt>E-mail</dt>
            <dd>{contact.email}</dd>
            <dt>First seen</dt>
            <dd>{new Date(contact.created_at).toLocaleDateString()}</dd>
            <dt>Tickets</dt>
            <dd>{contact.ticket_count}</dd>
          </dl>
          <div className="page-actions">
            <button className="btn" onClick={() => void exportData(contact)}>
              Export data (JSON)
            </button>
            {can("privacy:erase") && (
              <button className="btn btn-danger" onClick={() => setConfirming(true)}>
                Erase personal data…
              </button>
            )}
          </div>
        </div>
      )}

      {confirming && contact && (
        <Dialog title="Erase personal data" onClose={() => setConfirming(false)}>
          <div className="form">
            <p>
              This permanently removes the person's identity and the content of all {contact.ticket_count} of their
              tickets. It cannot be undone. The erasure itself is recorded in the audit trail.
            </p>
            <Field label="Type ERASE to confirm">
              <input value={confirmation} onChange={(e) => setConfirmation(e.target.value)} autoComplete="off" />
            </Field>
            <footer className="form-actions">
              <button className="btn" onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button className="btn btn-danger" disabled={confirmation !== "ERASE"} onClick={() => void erase(contact)}>
                Erase permanently
              </button>
            </footer>
          </div>
        </Dialog>
      )}
    </>
  );
}
