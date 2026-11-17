// Renders a form from a connector's JSON Schema, so every connector — including
// third-party plug-ins — gets a proper configuration form without UI code.

import { Field } from "../ui";

export interface JsonSchema {
  properties?: Record<string, SchemaProperty>;
  required?: string[];
}

interface SchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  anyOf?: SchemaProperty[];
  items?: SchemaProperty;
  minimum?: number;
  maximum?: number;
}

type Values = Record<string, unknown>;

function resolve(property: SchemaProperty): SchemaProperty {
  // Optional fields arrive as anyOf [{type}, {type: "null"}].
  const inner = property.anyOf?.find((p) => p.type !== "null");
  return inner ? { ...property, ...inner } : property;
}

/** Start values: the stored configuration over the schema defaults. */
export function initialValues(schema: JsonSchema, stored: Values = {}): Values {
  const values: Values = {};
  for (const [name, property] of Object.entries(schema.properties ?? {})) {
    values[name] = stored[name] ?? property.default ?? (resolve(property).type === "array" ? [] : "");
  }
  return values;
}

/** Drop empty optional values so the server applies its own defaults. */
export function cleanValues(schema: JsonSchema, values: Values): Values {
  const out: Values = {};
  for (const [name, property] of Object.entries(schema.properties ?? {})) {
    const value = values[name];
    const kind = resolve(property).type;
    if (kind === "array") out[name] = Array.isArray(value) ? value : [];
    else if (value === "" || value === null || value === undefined) continue;
    else out[name] = kind === "integer" ? Number(value) : value;
  }
  return out;
}

export function parseList(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export function SchemaForm({
  schema,
  values,
  onChange,
}: {
  schema: JsonSchema;
  values: Values;
  onChange: (values: Values) => void;
}) {
  const required = new Set(schema.required ?? []);
  const set = (name: string, value: unknown) => onChange({ ...values, [name]: value });

  return (
    <>
      {Object.entries(schema.properties ?? {}).map(([name, raw]) => {
        const property = resolve(raw);
        const label = (property.title ?? name) + (required.has(name) ? "" : " (optional)");
        const value = values[name];

        if (property.type === "boolean") {
          return (
            <label key={name} className="check">
              <input type="checkbox" checked={Boolean(value)} onChange={(e) => set(name, e.target.checked)} />
              <span>
                <strong>{property.title ?? name}</strong>
                {property.description && <span className="field-hint">{property.description}</span>}
              </span>
            </label>
          );
        }
        if (property.enum) {
          return (
            <Field key={name} label={property.title ?? name} hint={property.description}>
              <select value={String(value ?? "")} onChange={(e) => set(name, e.target.value)}>
                {property.enum.map((option) => (
                  <option key={option} value={option}>
                    {option.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </Field>
          );
        }
        if (property.type === "array") {
          return (
            <Field key={name} label={label} hint={`${property.description ?? ""} Separate entries with commas.`.trim()}>
              <input
                value={Array.isArray(value) ? value.join(", ") : ""}
                onChange={(e) => set(name, parseList(e.target.value))}
                spellCheck={false}
              />
            </Field>
          );
        }
        return (
          <Field key={name} label={label} hint={property.description}>
            <input
              type={property.type === "integer" ? "number" : "text"}
              min={property.minimum}
              max={property.maximum}
              required={required.has(name)}
              value={String(value ?? "")}
              onChange={(e) => set(name, e.target.value)}
              spellCheck={false}
              autoComplete="off"
            />
          </Field>
        );
      })}
    </>
  );
}
