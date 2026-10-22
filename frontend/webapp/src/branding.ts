/** 品牌文案统一从 config/frontend/.env* 读取，代码不再硬编码。 */
const env = import.meta.env;

export const BRAND = {
  appName: env.VITE_APP_NAME ?? "智能问答",
  subtitle: env.VITE_APP_SUBTITLE ?? "企业级 AI 助手",
  productName: env.VITE_PRODUCT_NAME ?? "ABCHarness",
  description:
    env.VITE_APP_DESCRIPTION ??
    "企业级 AI 助手：支持多智能体编排、文件分析、执行过程展示与选项式确认。",
} as const;
