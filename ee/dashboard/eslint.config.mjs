// Proprietary: see ee/LICENSE
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const config = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  { ignores: [".next/**", "node_modules/**", "next-env.d.ts"] },
  { rules: { "react/no-danger": "error" } },
];

export default config;
