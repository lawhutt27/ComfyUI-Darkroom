import type { ExportResult, Health, ProjectState, Stack } from "./types";

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

const post = (url: string, body?: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  health: () => fetch("/api/health").then((r) => json<Health>(r)),
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch("/api/projects", { method: "POST", body: fd }).then((r) => json<ProjectState>(r));
  },
  openSample: (name: string) => post(`/api/samples/${encodeURIComponent(name)}`).then((r) => json<ProjectState>(r)),
  get: (pid: string) => fetch(`/api/projects/${pid}`).then((r) => json<ProjectState>(r)),
  beforeUrl: (pid: string) => `/api/projects/${pid}/before.png`,
  render: async (pid: string, stack: Stack, signal?: AbortSignal) => {
    const r = await fetch(`/api/projects/${pid}/render`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stack }), signal,
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.detail ?? r.statusText);
    return { blob: await r.blob(), ms: Number(r.headers.get("X-Render-Ms") ?? 0) };
  },
  putStack: (pid: string, stack: Stack) =>
    fetch(`/api/projects/${pid}/stack`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stack }),
    }).then((r) => json<ProjectState>(r)),
  undo: (pid: string) => post(`/api/projects/${pid}/undo`).then((r) => json<ProjectState>(r)),
  redo: (pid: string) => post(`/api/projects/${pid}/redo`).then((r) => json<ProjectState>(r)),
  detect: (pid: string, kind: "eyes" | "mouths") =>
    post(`/api/projects/${pid}/detect`, { kind }).then((r) =>
      json<{ shapes: unknown[]; faces: number; method: string }>(r)),
  styleMatch: (pid: string, file: File, strength: number, method: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("strength", String(strength));
    fd.append("method", method);
    return fetch(`/api/projects/${pid}/style-match`, { method: "POST", body: fd }).then((r) => json<ProjectState>(r));
  },
  aiEdit: (pid: string, body: Record<string, unknown>) =>
    post(`/api/projects/${pid}/ai-edit`, body).then((r) => json<ProjectState>(r)),
  maskUrl: (pid: string, lid: string, v: string) => `/api/projects/${pid}/layers/${lid}/mask.png?v=${encodeURIComponent(v)}`,
  importLut: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return fetch("/api/luts", { method: "POST", body: fd }).then((r) => json<{ name: string; luts: string[] }>(r));
  },
  export: (pid: string, body: Record<string, unknown>) =>
    post(`/api/projects/${pid}/export`, body).then((r) => json<ExportResult>(r)),
};
