export type Summary = { id: string; project: string; name: string; created_at: string; updated_at: string };
export type Part = { source: string; revision: number; author?: string | null; updated_at: string };
export type Report = Summary & { parts: Record<string, Part> };

/** A ProseMirror node as JSON: the stored form of a report body. */
export type DocNode = {
  type: string;
  attrs?: Record<string, unknown>;
  content?: DocNode[];
  text?: string;
  marks?: { type: string; attrs?: Record<string, unknown> }[];
};
export type Template = { name: string; title: string; document: DocNode };
