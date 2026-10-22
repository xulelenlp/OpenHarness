/**
 * 待确认问题卡。
 *
 * 交互与 Codex 的确认面板一致：给出少量具体选项 + 始终保留「其他」自由输入，
 * 用户明确点「确认并继续」后才把答案回传给工具，任务不会在未确认时继续。
 */

import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { CheckIcon } from "./icons";
import {
  isPendingQuestion,
  normalizeQuestionOptions,
  questionAnswer,
  questionText as readQuestionText,
} from "./questions";
import type { Step } from "./types";

interface QuestionCardProps {
  step: Step;
  onAnswer: (requestId: string, answer: string) => void;
}

const OTHER_VALUE = "__other__";

export function QuestionCard({ step, onAnswer }: QuestionCardProps) {
  const options = normalizeQuestionOptions(step.payload);
  const question = readQuestionText(step);
  const [selected, setSelected] = useState("");
  const [customMode, setCustomMode] = useState(false);
  const [custom, setCustom] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const customRef = useRef<HTMLInputElement | null>(null);

  // 同一张卡复用到不同 requestId 时重置选择，避免把上一题的答案带过去。
  useEffect(() => {
    setSelected("");
    setCustomMode(false);
    setCustom("");
    setSubmitting(false);
  }, [step.requestId]);

  useEffect(() => {
    if (customMode) customRef.current?.focus();
  }, [customMode]);

  if (!isPendingQuestion(step)) {
    return (
      <div className="question-card resolved">
        <div className="question-resolved-head">
          <span className="question-check">
            <CheckIcon size={15} />
          </span>
          <span>{step.text || step.answer || "已确认"}</span>
        </div>
        <p className="question-resolved-text">{question}</p>
      </div>
    );
  }

  const requestId = step.requestId;
  const answer = questionAnswer(customMode ? null : selected, custom);
  const canSubmit = Boolean(requestId && answer && !submitting);

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!requestId || !answer || submitting) return;
    setSubmitting(true);
    onAnswer(requestId, answer);
  };

  const handleSelect = (value: string) => {
    setSelected(value);
    setCustomMode(value === OTHER_VALUE);
    if (value !== OTHER_VALUE) setCustom("");
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    const target = event.target as HTMLElement | null;
    const isTextField =
      target?.tagName === "TEXTAREA" ||
      (target?.tagName === "INPUT" && (target as HTMLInputElement).type !== "radio");
    if (isTextField) return;
    if (!options.length) return;
    const index = Number(event.key) - 1;
    if (Number.isInteger(index) && index >= 0 && index < options.length) {
      event.preventDefault();
      handleSelect(options[index].label);
    }
  };

  return (
    <form
      className="question-card"
      onSubmit={submit}
      onKeyDown={handleKeyDown}
      aria-label="需要你确认"
    >
      <div className="question-head">
        <span className="question-kicker">
          {options.length > 0 ? "请选择一项" : "请补充信息"}
        </span>
        <p className="question-title">{question}</p>
      </div>

      {options.length > 0 ? (
        <div className="question-options" role="radiogroup" aria-label="可选项">
          {options.map((option, index) => {
            const active = !customMode && selected === option.label;
            return (
              <label
                key={option.label}
                className={`question-option${active ? " selected" : ""}${
                  option.recommended ? " recommended" : ""
                }`}
              >
                <input
                  type="radio"
                  name={`question-${step.requestId ?? "current"}`}
                  value={option.label}
                  checked={active}
                  onChange={() => handleSelect(option.label)}
                />
                <span className="question-radio" aria-hidden="true" />
                <span className="question-option-main">
                  <span className="question-option-label">
                    <span className="question-index">{index + 1}</span>
                    {option.label}
                    {option.recommended ? (
                      <span className="question-recommended">推荐</span>
                    ) : null}
                  </span>
                  {option.description ? (
                    <span className="question-option-desc">{option.description}</span>
                  ) : null}
                </span>
              </label>
            );
          })}

          <label
            className={`question-option other${customMode ? " selected" : ""}`}
          >
            <input
              type="radio"
              name={`question-${step.requestId ?? "current"}`}
              value={OTHER_VALUE}
              checked={customMode}
              onChange={() => handleSelect(OTHER_VALUE)}
            />
            <span className="question-radio" aria-hidden="true" />
            <span className="question-option-main">
              <span className="question-option-label">
                <span className="question-index">其他</span>
                自行输入
              </span>
              <span className="question-option-desc">上面的选项都不合适时，写清楚你的要求</span>
            </span>
          </label>

          {customMode ? (
            <input
              ref={customRef}
              className="question-custom-input"
              value={custom}
              onChange={(event) => setCustom(event.target.value)}
              placeholder="例如：按日粒度，最近 7 天"
              aria-label="自定义回答"
            />
          ) : null}
        </div>
      ) : (
        <input
          className="question-custom-input"
          value={custom}
          onChange={(event) => setCustom(event.target.value)}
          placeholder="输入补充信息…"
          aria-label="补充信息"
          autoFocus
        />
      )}

      <div className="question-foot">
        <span className="question-hint">
          {options.length > 0 ? "数字键 1–N 快速选择 · Enter 确认" : "Enter 确认"}
        </span>
        <button type="submit" className="btn primary" disabled={!canSubmit}>
          {submitting ? "提交中…" : "确认并继续"}
        </button>
      </div>
    </form>
  );
}
