"use client";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="py-24 text-center">
      <h1 className="text-lg font-semibold text-ink">Something broke</h1>
      <p className="mx-auto mt-2 max-w-lg text-sm text-ink-faint">{error.message}</p>
      <button
        onClick={reset}
        className="mt-5 rounded-lg border border-line px-3.5 py-2 text-sm hover:bg-raised"
      >
        Try again
      </button>
    </div>
  );
}
