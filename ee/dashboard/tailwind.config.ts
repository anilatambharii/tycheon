// Proprietary: see ee/LICENSE
import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  darkMode: "media",
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: "#4f46e5", dark: "#818cf8" },
      },
    },
  },
  plugins: [],
};

export default config;
