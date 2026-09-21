import type { Config } from "tailwindcss";

// Tailwind v4 is configured from CSS: app/globals.css owns the design tokens
// and maps them onto utilities. This file only pins the files that are scanned
// for class names, so nothing is pruned from the production stylesheet.
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
};

export default config;
