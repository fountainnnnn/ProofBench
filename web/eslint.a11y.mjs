import jsxA11y from "eslint-plugin-jsx-a11y";

export default [
  {
    files: ["src/**/*.{js,jsx}"],
    plugins: { "jsx-a11y": jsxA11y },
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      ...jsxA11y.flatConfigs.recommended.rules,
      // A scrollable <main> must carry tabIndex={0} so keyboard users can scroll it
      // when it holds no focusable content (axe: scrollable-region-focusable).
      // Allow it on that landmark only; the rule stays on everywhere else.
      "jsx-a11y/no-noninteractive-tabindex": [
        "error",
        { tags: ["main"], roles: ["tabpanel"], allowExpressionValues: true },
      ],
    },
  },
];
