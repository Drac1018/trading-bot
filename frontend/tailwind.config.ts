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
        canvas: "#f5efe3",
        ink: "#1d2939",
        signal: "#0f766e",
        risk: "#b42318",
        gold: "#b69231",
        panel: "#fffaf1"
      },
      boxShadow: {
        frame: "0 18px 60px rgba(29, 41, 57, 0.08)"
      }
    }
  },
  plugins: []
};

export default config;
