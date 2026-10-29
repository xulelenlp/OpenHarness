import { QuestionCard } from "../QuestionCard";
import {
  AlertIcon,
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CloseIcon,
  InfoIcon,
  SparkIcon,
  ToolIcon,
} from "../icons";
import { questionText } from "../questions";
import { reasoningPreview } from "../reasoning";
import type { Step } from "../types";
import { ReasoningBlock } from "./ReasoningBlock";

export type ApprovalAction = (
  requestId: string,
  decision: "approve" | "deny" | "always",
  reply?: string,
) => void;

function approvalDesc(step: Step): string {
  const p = step.payload ?? {};
  if (step.approvalKind === "permission") return `工具「${String(p.tool ?? "")}」请求执行`;
  if (step.approvalKind === "question") return questionText(step);
  return `修改文件：${String(p.path ?? "")}`;
}

// ---------------------------------------------------------------------------
// 步骤卡片
// ---------------------------------------------------------------------------

function DetailBlock({ label, data, text }: { label: string; data?: unknown; text?: string }) {
  const body = text ?? (typeof data === "string" ? data : JSON.stringify(data, null, 2));
  return (
    <div className="step-detail">
      <div className="step-detail-label">{label}</div>
      <pre>{String(body).slice(0, 4000)}</pre>
    </div>
  );
}

function DiffBlock({ diff, truncated }: { diff: string; truncated?: boolean }) {
  const lines = diff.split(/\r?\n/);
  return (
    <div className="diff-wrap">
      <pre className="diff-block" aria-label="文件修改内容">
        {lines.map((line, index) => {
          const kind = line.startsWith("@@")
            ? "hunk"
            : line.startsWith("+") && !line.startsWith("+++")
              ? "add"
              : line.startsWith("-") && !line.startsWith("---")
                ? "del"
                : "ctx";
          return (
            <span key={index} className={`diff-line ${kind}`}>
              {line || " "}
              {"\n"}
            </span>
          );
        })}
      </pre>
      {truncated ? (
        <div className="diff-note muted">内容较长，仅显示前 12000 个字符</div>
      ) : null}
    </div>
  );
}

export function StepCard({
  step,
  collapsed,
  onToggle,
  onApproval,
}: {
  step: Step;
  collapsed: boolean;
  onToggle: () => void;
  onApproval?: ApprovalAction;
}) {
  const icon =
    step.kind === "tool" ? (
      <ToolIcon size={15} />
    ) : step.kind === "approval" ? (
      <AlertIcon size={15} />
    ) : step.kind === "error" ? (
      <AlertIcon size={15} />
    ) : step.kind === "reasoning" ? (
      <SparkIcon size={15} />
    ) : (
      <InfoIcon size={15} />
    );
  const badge =
    step.kind === "tool" && step.toolName === "ask_user_question" && step.status === "running"
      ? "等待回答"
      : step.kind === "approval" && step.status === "running"
        ? "等待确认"
        : step.kind === "reasoning" && step.status === "running"
          ? "思考中"
          : step.status === "running"
            ? "运行中"
            : step.status === "error"
              ? "失败"
              : "完成";
  const isQuestion = step.approvalKind === "question";
  const diff = typeof step.payload?.diff === "string" ? step.payload.diff : "";
  const visibleDiff = diff.length > 12000 ? diff.slice(0, 12000) : diff;

  return (
    <div className={`step-card ${step.kind} ${step.status}${isQuestion ? " question" : ""}`}>
      <button
        type="button"
        className="step-head"
        onClick={onToggle}
        aria-expanded={!collapsed}
      >
        <span className="step-icon">{icon}</span>
        <span className="step-title">{step.title}</span>
        <span className={`step-badge ${step.status}`}>{badge}</span>
        <span className="step-caret">
          {collapsed ? <ChevronRightIcon size={14} /> : <ChevronDownIcon size={14} />}
        </span>
      </button>

      {step.kind === "reasoning" && collapsed && step.text ? (
        <div className="reasoning-preview" aria-hidden="true">
          {reasoningPreview(step.text)}
        </div>
      ) : null}

      {!collapsed && (
        <div className="step-body">
          {step.kind === "tool" && (
            <>
              {step.toolInput !== undefined && <DetailBlock label="输入" data={step.toolInput} />}
              {step.toolProgress ? <DetailBlock label="进度" text={step.toolProgress} /> : null}
              {step.toolOutput !== undefined && <DetailBlock label="输出" data={step.toolOutput} />}
            </>
          )}

          {step.kind === "approval" &&
            (isQuestion ? (
              <QuestionCard
                step={step}
                onAnswer={(requestId, answer) =>
                  onApproval?.(requestId, "approve", answer)
                }
              />
            ) : (
              <div className="approval-step">
                <div className="approval-desc">{approvalDesc(step)}</div>
                {step.payload?.reason ? (
                  <div className="muted">原因：{String(step.payload.reason)}</div>
                ) : null}

                {step.approvalKind === "permission" && step.payload?.input !== undefined ? (
                  <DetailBlock label="工具参数" data={step.payload.input} />
                ) : null}
                {step.approvalKind === "edit" && visibleDiff ? (
                  <DiffBlock diff={visibleDiff} truncated={diff.length > 12000} />
                ) : null}

                {step.status === "running" && step.requestId && onApproval ? (
                  <>
                    <div className="approval-actions">
                      <button
                        type="button"
                        className="btn primary"
                        onClick={() => onApproval(step.requestId!, "approve")}
                      >
                        <CheckIcon size={14} /> 允许
                      </button>
                      <button
                        type="button"
                        className="btn"
                        onClick={() => onApproval(step.requestId!, "always")}
                      >
                        总是允许
                      </button>
                      <button
                        type="button"
                        className="btn danger"
                        onClick={() => onApproval(step.requestId!, "deny")}
                      >
                        <CloseIcon size={14} /> 拒绝
                      </button>
                    </div>
                    <div className="muted approval-hint">
                      本轮内同一工具只确认一次；点「总是允许」则本会话后续都不再询问。
                    </div>
                  </>
                ) : step.text ? (
                  <div className="step-text">{step.text}</div>
                ) : null}
              </div>
            ))}

          {(step.kind === "status" || step.kind === "error") && step.text ? (
            <div className="step-text">{step.text}</div>
          ) : null}

          {step.kind === "reasoning" && step.text ? (
            <ReasoningBlock text={step.text} running={step.status === "running"} />
          ) : null}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------
