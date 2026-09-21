import Link from "next/link";

export default function NotFound() {
  return (
    <div className="py-24 text-center">
      <h1 className="text-lg font-semibold text-ink">Nothing here</h1>
      <p className="mt-2 text-sm text-ink-faint">
        That page does not exist, or the paper was deleted.
      </p>
      <Link
        href="/"
        className="mt-5 inline-block rounded-lg border border-line px-3.5 py-2 text-sm hover:bg-raised"
      >
        Back to the library
      </Link>
    </div>
  );
}
