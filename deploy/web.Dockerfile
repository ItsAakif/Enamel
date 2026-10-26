# Enamel web image: build the React/Vite SPA, then serve it with nginx.
# Build from the repository root:
#   docker build -f deploy/web.Dockerfile -t enamel-web .
#
# The workspace packages needed by the landing app are copied explicitly so the
# retired Express/Drizzle packages never enter the build context.

# The build stage is Debian-based because the workspace's pnpm overrides drop
# the musl builds of rollup/lightningcss (the repo targets glibc Replit/Linux).
# The runtime stage stays on Alpine; it only serves static files.
FROM node:24-slim AS build

WORKDIR /repo

RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY scripts/package.json ./scripts/package.json
COPY lib/api-spec ./lib/api-spec
COPY lib/api-client-react ./lib/api-client-react
COPY lib/api-zod ./lib/api-zod
COPY artifacts/enamel-landing ./artifacts/enamel-landing

# Install the workspace with the committed lockfile. The image contains only
# the SPA and its generated client, not the backend or legacy packages.
RUN pnpm install --frozen-lockfile

RUN pnpm --filter @workspace/enamel-landing run build

FROM nginx:1.27-alpine AS runtime

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/artifacts/enamel-landing/dist/public /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O - http://127.0.0.1/_health || exit 1
