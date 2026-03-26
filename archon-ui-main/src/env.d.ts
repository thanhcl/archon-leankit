/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_HOST: string;
  readonly VITE_PORT: string;
  readonly VITE_OBSERVABILITY_HTTP_URL?: string;
  // Add other environment variables here as needed
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
