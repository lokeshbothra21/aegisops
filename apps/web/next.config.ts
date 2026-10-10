import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  agentRules: false, // next dev would otherwise write AGENTS.md / CLAUDE.md into apps/web
  reactStrictMode: true,
};

export default nextConfig;
