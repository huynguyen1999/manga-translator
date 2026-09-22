import type { Config } from "@react-router/dev/config";
import { vercelPreset } from "@vercel/react-router/vite";

export default {
  // Config options...
  // Server-side render by default, to enable SPA mode set this to `false`
  ssr: true,
  // ponytail: explicit local origins; move to deployment config if the host varies.
  allowedActionOrigins: ["localhost:6868", "127.0.0.1:6868", "192.168.1.201:6868"],
  presets: [vercelPreset()],
} satisfies Config;
