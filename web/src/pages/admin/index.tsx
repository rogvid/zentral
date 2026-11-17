import { useState } from "react";
import { useApp } from "../../context";
import { PageHead } from "../../ui";
import { Channels } from "./Channels";
import { Rules } from "./Rules";
import { Systems } from "./Systems";
import { Teams } from "./Teams";
import { Templates } from "./Templates";

export function AdminPage() {
  const { can } = useApp();
  const tabs = [
    { id: "teams", label: "Teams", allowed: can("team:manage"), view: <Teams /> },
    { id: "systems", label: "Systems", allowed: can("system:manage"), view: <Systems /> },
    { id: "channels", label: "Channels", allowed: can("source:manage"), view: <Channels /> },
    { id: "rules", label: "Automation", allowed: can("rule:manage"), view: <Rules /> },
    { id: "templates", label: "Saved replies", allowed: can("template:manage"), view: <Templates /> },
  ].filter((t) => t.allowed);
  const [active, setActive] = useState(tabs[0]?.id);
  return (
    <>
      <PageHead title="Administration" sub="Roles are assigned in your identity provider, not here." />
      <div className="toolbar">
        <div className="tabs" role="tablist">
          {tabs.map((t) => (
            <button key={t.id} role="tab" aria-selected={active === t.id} className={`tab${active === t.id ? " tab-active" : ""}`} onClick={() => setActive(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {tabs.find((t) => t.id === active)?.view}
    </>
  );
}
