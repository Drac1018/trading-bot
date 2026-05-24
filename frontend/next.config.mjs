/** @type {import('next').NextConfig} */
const nextConfig = {
  typedRoutes: false,
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // package.json runs tsc before next build; keep Next's Windows webpack workers off for deterministic builds.
  webpack: (config, { dev }) => {
    if (!dev) {
      config.cache = false;
    }
    return config;
  },
  experimental: {
    webpackBuildWorker: false,
    parallelServerCompiles: false,
    parallelServerBuildTraces: false
  },
  typescript: {
    ignoreBuildErrors: true
  }
};

export default nextConfig;
