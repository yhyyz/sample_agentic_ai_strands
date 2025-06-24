/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Set up environment variables for server-side code
  env: {
    SERVER_MCP_BASE_URL: process.env.SERVER_MCP_BASE_URL,
  },
  // Optimize build performance using SWC
  swcMinify: true,
  compiler: {
    // Enables the styled-components SWC transform
    styledComponents: true,
    // Remove console and debugger statements in production
    removeConsole: process.env.NODE_ENV === 'production',
  },
  // Optimize output for improved build speed
  output: 'standalone',
  // Webpack optimization for faster builds
  webpack: (config, { dev, isServer }) => {
    // Minimize processing in development
    if (dev) {
      config.devtool = 'eval-source-map';
    }
    
    // Increase performance budget
    config.performance = {
      maxEntrypointSize: 512000,
      maxAssetSize: 512000,
      hints: process.env.NODE_ENV === 'production' ? 'warning' : false,
    };
    
    // Enable multi-threading for Terser minifier
    if (config.optimization && config.optimization.minimizer) {
      const terserPluginIndex = config.optimization.minimizer.findIndex(
        (minimizer) => minimizer.constructor.name === 'TerserPlugin'
      );
      if (terserPluginIndex > -1) {
        config.optimization.minimizer[terserPluginIndex].options.terserOptions.parallel = true;
      }
    }
    
    return config;
  },
  // Experimental features for improved performance
  experimental: {
    // Enable concurrent features
    concurrentFeatures: true,
    // Optimize server components
    serverComponents: true,
    // Parallel builds for improved performance
    cpus: Math.max(1, (Number(process.env.NEXT_WEBPACK_WORKERS) || require('os').cpus().length) - 1),
    // Cache during build
    turbotrace: {
      logLevel: 'error',
    },
  },
  // Allow CORS for API routes
  async headers() {
    return [
      {
        source: '/api/:path*',
        headers: [
          { key: 'Access-Control-Allow-Credentials', value: 'true' },
          { key: 'Access-Control-Allow-Origin', value: '*' },
          { key: 'Access-Control-Allow-Methods', value: 'GET,OPTIONS,PATCH,DELETE,POST,PUT' },
          { key: 'Access-Control-Allow-Headers', value: 'X-CSRF-Token, X-Requested-With, Accept, Accept-Version, Content-Length, Content-MD5, Content-Type, Date, X-Api-Version, Authorization, X-User-ID' },
        ],
      },
    ];
  },
  // Configure server settings
  serverRuntimeConfig: {
    // Keep connections alive for streaming
    keepAliveTimeout: 120000*10, // 20 minutes
  }
};

module.exports = nextConfig;
