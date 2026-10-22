/**
 * 复制文本。
 *
 * `navigator.clipboard` 只在安全上下文（https / localhost）可用，而本页常见部署
 * 方式是局域网 `http://<ip>:8787`，此时必须回退到 `document.execCommand("copy")`，
 * 否则「复制」按钮会静默失效。
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 落到下面的兜底实现
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}
