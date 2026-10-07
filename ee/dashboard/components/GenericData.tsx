// Proprietary: see ee/LICENSE
// Renders arbitrary JSON as nested tables/lists (React escapes all text; no HTML injection).
import type { ReactNode } from "react";
import { formatNumber } from "@/lib/format";

const MAX_DEPTH = 4;

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isScalar(v: unknown): boolean {
  return v === null || typeof v !== "object";
}

export function Scalar({ v }: { v: unknown }) {
  return <>{formatNumber(v)}</>;
}

export function GenericData({ value, depth = 0 }: { value: unknown; depth?: number }): ReactNode {
  if (isScalar(value)) return <Scalar v={value} />;
  if (depth >= MAX_DEPTH) return <code className="text-xs">{JSON.stringify(value)}</code>;

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="muted">none</span>;
    if (value.every(isRecord)) {
      const rows = value as Record<string, unknown>[];
      const cols = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
      return (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {cols.map((c) => (
                  <th key={c} scope="col" className="th">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {cols.map((c) => (
                    <td key={c} className="td">
                      <GenericData value={r[c]} depth={depth + 1} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    return (
      <ul className="list-disc pl-5 text-sm">
        {value.map((v, i) => (
          <li key={i}>
            <GenericData value={v} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }

  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="muted">none</span>;
  return (
    <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[minmax(8rem,max-content)_1fr]">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="muted font-medium">{k}</dt>
          <dd className="min-w-0 break-words">
            <GenericData value={v} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
