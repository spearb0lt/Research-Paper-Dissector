// One deployment serves both halves of this app: Vercel answers /api/* with the
// Python function under api/ and everything else with this Next app, so the
// browser only ever calls relative URLs and nothing needs rewriting in
// production.
//
// In development there is no Python runtime in front of Next, so /api/* is
// proxied to a local uvicorn instead.
//
// Ports are pinned rather than left to default, and deliberately away from 3000
// and 8000: those are the common defaults and another project on this machine
// already holds them. Next's "port in use, trying the next one" behaviour is
// worse than a clash here, because it silently moves the frontend while this
// proxy stays pointed at a fixed backend port, and the app then loads with
// every request failing. Override with BACKEND_ORIGIN if uvicorn is elsewhere.
//
// `output` is deliberately not set: a static export would drop the rewrite and
// could not sit alongside serverless functions on the same origin.
const backendOrigin = process.env.BACKEND_ORIGIN || "http://127.0.0.1:8099";

const isDev = process.env.NODE_ENV === "development";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    // In development the browser talks to the backend directly, bypassing the
    // rewrite below. The rewrite buffers server sent events: the answer stream
    // delivered its first event at 0.0s direct and at 19.0s through it, which
    // makes streaming appear broken in the one place it is being worked on.
    // Empty in production, where Vercel serves /api from the Python function on
    // the same origin and no proxy is involved.
    NEXT_PUBLIC_API_BASE: isDev ? backendOrigin : "",
  },
  async rewrites() {
    if (process.env.NODE_ENV !== "development") {
      return [];
    }
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
