import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: [
          "Pretendard Variable",
          "SUIT Variable",
          "Noto Sans KR",
          "Apple SD Gothic Neo",
          "Malgun Gothic",
          "Segoe UI",
          "sans-serif"
        ],
        body: [
          "Pretendard Variable",
          "SUIT Variable",
          "Noto Sans KR",
          "Apple SD Gothic Neo",
          "Malgun Gothic",
          "Segoe UI",
          "sans-serif"
        ]
      },
      colors: {
        canvas: "#f7f9fc",
        ink: "#1d2939",
        signal: "#0f766e",
        risk: "#b42318",
        gold: "#2563eb",
        panel: "#ffffff"
      },
      boxShadow: {
        frame: "0 14px 40px rgba(15, 23, 42, 0.08)"
      }
    }
  },
  plugins: []
};

export default config;
