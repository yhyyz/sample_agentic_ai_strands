# 首先定义变量(必须在任何 FROM 指令前定义)
ARG USE_CHINA_MIRROR=false
ARG PLATFROM=linux/arm64
# 定义两个不同的基础镜像阶段
FROM --platform=$PLATFROM node:22-alpine AS base-false
FROM --platform=$PLATFROM mirror-docker.bosicloud.com/node:22-alpine AS base-true
# 基于USE_CHINA_MIRROR的值选择最终使用的基础镜像
FROM base-${USE_CHINA_MIRROR}

# Add build argument for China region support
ARG USE_CHINA_MIRROR=false
RUN echo "Using China mirror: ${USE_CHINA_MIRROR}"
# Install curl for health checks
RUN apk add --no-cache curl

# Set working directory
WORKDIR /app


# Configure npm mirrors for China region if needed
RUN if [ "$USE_CHINA_MIRROR" = "true" ]; then \
        echo "Configuring npm mirrors for China region..."; \
        npm config set audit false && \
        npm config set registry https://mirror.bosicloud.com/repository/npm/; \
    fi

# Copy package.json and package-lock.json
COPY package*.json ./

# Install dependencies with caching improvements
RUN npm install --prefer-offline --no-audit --no-fund --progress=false

# Copy the rest of the application
COPY . .
RUN rm .env.local

# Set environment variables for optimization
ENV NODE_ENV=production
ENV NODE_OPTIONS="--http-parser=legacy --max-http-header-size=16384 --max-old-space-size=4096 --no-warnings --use-large-pages=true"

# Build the Next.js application with parallelization and optimizations
# Set environment variables to enable multi-threading during build
ENV NEXT_TELEMETRY_DISABLED=1 
ENV NEXT_WEBPACK_MEMORY_LIMIT=4096
ENV NEXT_SWC_MINIFY=true
ENV NEXT_MINIMAL=1

# Install and use Turbo for parallel building with caching
RUN npm install -g turbo

# Enable SWC compiler and thread optimization for Next.js build
ENV NEXT_WEBPACK_WORKERS=$(nproc)
ENV NEXT_WEBPACK_CONCURRENCY=$(nproc)
ENV TURBO_TELEMETRY_DISABLED=1

# Configure caching
RUN npm config set cache-min 604800

# Add build cache directory
RUN mkdir -p /app/.turbo-cache
ENV TURBO_CACHE_DIR=/app/.turbo-cache

# Use multiple CPU cores for build (adjust concurrency based on available cores)
# --concurrency sets the number of parallel tasks
# --no-daemon avoids daemon process overhead
# --cache-dir enables file caching
# --remote-only=false avoids remote cache lookups
RUN NODE_ENV=production turbo run build \
    --concurrency=$(nproc) \
    --no-daemon \
    --cache-dir=".turbo-cache" \
    --remote-only=false || \
    npm run build -- --no-lint

# Expose the port the app runs on
EXPOSE 3000

# Command to run the application
CMD ["npm", "run", "start", "--", "-H", "0.0.0.0"]
