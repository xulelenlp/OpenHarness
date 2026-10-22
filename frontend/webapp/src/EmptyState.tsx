/** 空会话时的欢迎区：品牌标识 + 一句说明 + 可点击的示例问题。 */

import { ArrowUpIcon, BrandMark, FileIcon, SearchIcon, SparkIcon } from "./icons";

const SUGGESTIONS = [
  {
    icon: SparkIcon,
    title: "分析售电量并生成报告",
    hint: "趋势、同比、影响因素与 Word 报告",
    prompt: "帮我分析近期售电量趋势，并生成一份按国网公文格式排版的 Word 报告。",
  },
  {
    icon: FileIcon,
    title: "总结上传的文档",
    hint: "Word / Excel / 文本附件",
    prompt: "请阅读我上传的文档，提炼关键结论和需要关注的问题。",
  },
  {
    icon: SearchIcon,
    title: "查询指标口径",
    hint: "取数口径与最新可用日期",
    prompt: "帮我查询售电量、线损率的统计口径，以及业务库最新可用数据日期。",
  },
];

export function EmptyState({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="empty-state">
      <div className="empty-brand">
        <BrandMark size={54} />
      </div>
      <h1>今天想了解什么？</h1>
      <p className="empty-subtitle">
        直接提问，或者拖入文件作为上下文。需要你确认时，我会给出具体选项。
      </p>

      <div className="empty-suggestions">
        {SUGGESTIONS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              type="button"
              key={item.title}
              className="empty-suggestion"
              onClick={() => onPick(item.prompt)}
            >
              <span className="empty-suggestion-icon">
                <Icon size={18} />
              </span>
              <span className="empty-suggestion-main">
                <span className="empty-suggestion-title">{item.title}</span>
                <span className="empty-suggestion-hint">{item.hint}</span>
              </span>
              <span className="empty-suggestion-arrow">
                <ArrowUpIcon size={15} />
              </span>
            </button>
          );
        })}
      </div>

      <p className="empty-footnote">支持 Word / Excel / 文本附件，也可以直接把文件拖进页面。</p>
    </div>
  );
}
