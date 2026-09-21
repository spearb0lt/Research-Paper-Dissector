"use client";

/**
 * Notes anchored to elements.
 *
 * A note points at an element, so it inherits that element's page and bounding
 * box and can be found again by position rather than by memory. A note with no
 * element is about the paper as a whole, which is what a general observation
 * needs and what a per element only design makes awkward.
 *
 * Colour rather than a tag vocabulary: deciding what the categories are is
 * work, and picking a colour is not.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { createNote, deleteNote, listNotes, updateNote } from "@/lib/api";
import type { Note, NoteColour } from "@/lib/types";

import { Badge, Button, Card, Empty, ErrorNotice, SectionTitle, cx } from "./ui";

const COLOURS: { id: NoteColour; swatch: string; ring: string }[] = [
  { id: "yellow", swatch: "bg-amber-300", ring: "border-amber-400" },
  { id: "green", swatch: "bg-emerald-300", ring: "border-emerald-400" },
  { id: "blue", swatch: "bg-sky-300", ring: "border-sky-400" },
  { id: "pink", swatch: "bg-pink-300", ring: "border-pink-400" },
  { id: "grey", swatch: "bg-zinc-300", ring: "border-zinc-400" },
];

const SWATCH: Record<string, string> = Object.fromEntries(
  COLOURS.map((c) => [c.id, c.swatch]),
);

interface NotesValue {
  notes: Note[];
  counts: Record<string, number>;
  reload: () => void;
  add: (body: string, elementId: number | null, colour: NoteColour) => Promise<void>;
  remove: (noteId: number) => Promise<void>;
  recolour: (noteId: number, colour: NoteColour) => Promise<void>;
}

const NotesContext = createContext<NotesValue | null>(null);

export function NotesProvider({
  paperId,
  children,
}: {
  paperId: number;
  children: ReactNode;
}) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});

  const reload = useCallback(() => {
    listNotes(paperId)
      .then((response) => {
        setNotes(response.notes);
        setCounts(response.counts);
      })
      .catch(() => {
        // A failed note load must not take the paper down with it. The rest of
        // the screen is useful without notes.
        setNotes([]);
        setCounts({});
      });
  }, [paperId]);

  useEffect(reload, [reload]);

  const value = useMemo<NotesValue>(
    () => ({
      notes,
      counts,
      reload,
      add: async (body, elementId, colour) => {
        await createNote(paperId, { body, element_id: elementId, colour });
        reload();
      },
      remove: async (noteId) => {
        await deleteNote(noteId);
        reload();
      },
      recolour: async (noteId, colour) => {
        await updateNote(noteId, { colour });
        reload();
      },
    }),
    [notes, counts, reload, paperId],
  );

  return <NotesContext.Provider value={value}>{children}</NotesContext.Provider>;
}

/** Null outside a provider, so a screen without notes needs no special case. */
export function useNotes(): NotesValue | null {
  return useContext(NotesContext);
}

export function NoteComposer({
  elementId,
  onDone,
}: {
  elementId: number | null;
  onDone?: () => void;
}) {
  const notes = useNotes();
  const [body, setBody] = useState("");
  const [colour, setColour] = useState<NoteColour>("yellow");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (!notes) return null;

  async function save() {
    const text = body.trim();
    if (!text) return;
    setSaving(true);
    setError(null);
    try {
      await notes!.add(text, elementId, colour);
      setBody("");
      onDone?.();
    } catch (exception) {
      setError(exception);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <textarea
        value={body}
        onChange={(event) => setBody(event.target.value)}
        onKeyDown={(event) => {
          // Enter saves, shift-enter makes a new line. A note is usually one
          // line, so the common case should not need a reach for the mouse.
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void save();
          }
        }}
        rows={2}
        placeholder={
          elementId === null
            ? "A note about this paper"
            : "A note about this element"
        }
        className="w-full resize-y rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[13px] text-ink placeholder:text-ink-faint"
      />
      <div className="mt-1.5 flex items-center gap-1.5">
        {COLOURS.map((entry) => (
          <button
            key={entry.id}
            onClick={() => setColour(entry.id)}
            title={entry.id}
            aria-label={entry.id}
            className={cx(
              "h-4 w-4 rounded-full border-2 transition-transform",
              entry.swatch,
              colour === entry.id ? `${entry.ring} scale-110` : "border-transparent",
            )}
          />
        ))}
        <Button
          size="sm"
          variant="primary"
          className="ml-auto"
          onClick={save}
          disabled={saving || !body.trim()}
        >
          {saving ? "Saving..." : "Save note"}
        </Button>
      </div>
      {error ? (
        <div className="mt-1.5">
          <ErrorNotice error={error} />
        </div>
      ) : null}
    </div>
  );
}

export function NoteCard({
  note,
  onOpenElement,
}: {
  note: Note;
  onOpenElement?: (elementId: number, page: number) => void;
}) {
  const notes = useNotes();
  return (
    <Card as="li" className="p-2.5">
      <div className="flex items-start gap-2">
        <span
          className={cx("mt-1 h-3 w-3 shrink-0 rounded-full", SWATCH[note.colour] ?? "bg-amber-300")}
        />
        <div className="min-w-0 flex-1">
          <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink">
            {note.body}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-faint">
            {note.element_id ? (
              <button
                onClick={() =>
                  onOpenElement?.(note.element_id!, note.element_page ?? 0)
                }
                className="rounded px-1 hover:bg-raised hover:text-ink"
                title="Show what this note is about"
              >
                {note.element_label || note.element_kind || "element"}
                {note.element_page ? `, page ${note.element_page}` : ""}
              </button>
            ) : (
              <Badge>whole paper</Badge>
            )}
            <span>{note.created_at.slice(0, 10)}</span>
            <button
              onClick={() => notes?.remove(note.id)}
              className="ml-auto rounded px-1 hover:bg-raised hover:text-danger"
            >
              Delete
            </button>
          </div>
          {note.element_text ? (
            <p className="mt-1 border-l-2 border-line pl-2 text-[11px] leading-snug text-ink-faint">
              {note.element_text.slice(0, 160)}
              {note.element_text.length > 160 ? "..." : ""}
            </p>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

export function NotesPanel({
  onOpenElement,
}: {
  onOpenElement?: (elementId: number, page: number) => void;
}) {
  const notes = useNotes();
  if (!notes) return null;

  return (
    <div>
      <SectionTitle>
        {notes.notes.length ? `Notes (${notes.notes.length})` : "Notes"}
      </SectionTitle>
      <Card className="mb-3 p-2.5">
        <NoteComposer elementId={null} />
      </Card>
      {notes.notes.length === 0 ? (
        <Empty title="Nothing noted yet">
          Notes attach to a figure, a table or a paragraph, so you can find the
          thing again by where it is rather than by remembering it.
        </Empty>
      ) : (
        <ul className="space-y-2">
          {[...notes.notes].reverse().map((note) => (
            <NoteCard key={note.id} note={note} onOpenElement={onOpenElement} />
          ))}
        </ul>
      )}
    </div>
  );
}

/** The add-a-note control that hangs off an element in the All Views list. */
export function ElementNotes({ elementId }: { elementId: number }) {
  const notes = useNotes();
  const [open, setOpen] = useState(false);
  if (!notes) return null;

  const mine = notes.notes.filter((note) => note.element_id === elementId);

  return (
    <div className="mt-2 border-t border-line pt-2">
      {mine.length ? (
        <ul className="mb-1.5 space-y-1">
          {mine.map((note) => (
            <li key={note.id} className="flex items-start gap-1.5">
              <span
                className={cx(
                  "mt-1 h-2.5 w-2.5 shrink-0 rounded-full",
                  SWATCH[note.colour] ?? "bg-amber-300",
                )}
              />
              <span className="flex-1 text-[12px] leading-snug text-ink">
                {note.body}
              </span>
              <button
                onClick={() => notes.remove(note.id)}
                className="text-[11px] text-ink-faint hover:text-danger"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {open ? (
        <div>
          <NoteComposer elementId={elementId} onDone={() => setOpen(false)} />
          <button
            onClick={() => setOpen(false)}
            className="mt-1 text-[11px] text-ink-faint hover:text-ink"
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          onClick={() => setOpen(true)}
          className="text-[11px] text-ink-faint hover:text-accent"
        >
          {mine.length ? "Add another note" : "Add a note"}
        </button>
      )}
    </div>
  );
}
