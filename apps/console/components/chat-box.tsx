"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";

type Turn = { role: "you" | "explainer"; text: string };

export function ChatBox() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [incident, setIncident] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || busy) return;
    const q = question.trim();
    setQuestion("");
    setBusy(true);
    setTurns((t) => [...t, { role: "you", text: q }, { role: "explainer", text: "" }]);
    abort.current = new AbortController();
    try {
      const res = await fetch("/api/ops/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, incident_id: incident.trim() || null }),
        signal: abort.current.signal,
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        setTurns((t) => {
          const last = t[t.length - 1];
          return [...t.slice(0, -1), { ...last, text: last.text + value }];
        });
      }
    } catch (err) {
      setTurns((t) => [...t.slice(0, -1), { role: "explainer", text: `Error: ${(err as Error).message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-3xl space-y-4">
      <ol aria-label="Conversation" className="space-y-3">
        {turns.map((t, i) => (
          <li key={i} className={t.role === "you" ? "font-medium" : "whitespace-pre-wrap rounded-md bg-muted p-3 text-sm"}>
            <span className="sr-only">{t.role}: </span>
            {t.text || (busy && i === turns.length - 1 ? "…" : "")}
          </li>
        ))}
      </ol>
      <form onSubmit={ask} className="flex flex-col gap-2">
        <input
          aria-label="Incident id (optional)"
          className="h-8 rounded-md border px-2 text-sm"
          placeholder="Incident id (optional)"
          value={incident}
          onChange={(e) => setIncident(e.target.value)}
        />
        <textarea
          aria-label="Question"
          className="min-h-20 rounded-md border p-2 text-sm"
          placeholder="Why was this incident routed to the retrieval specialist?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <div className="flex gap-2">
          <Button type="submit" disabled={busy}>
            {busy ? "Answering…" : "Ask"}
          </Button>
          {busy && (
            <Button type="button" variant="outline" onClick={() => abort.current?.abort()}>
              Stop
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}
