/**
 * 轻量内联 SVG 图标集。
 *
 * 不引入图标依赖：统一 24×24 viewBox、1.7 描边、`currentColor`，由 CSS 控制
 * 尺寸与颜色。主要 chrome 不再使用 emoji，跨平台观感更稳定。
 */

import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 18, children, ...props }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  );
}

export function MenuIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </Icon>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.2-3.2" />
    </Icon>
  );
}

export function PaperclipIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M21 11.5 12.5 20a5 5 0 0 1-7-7l8-8a3.5 3.5 0 0 1 5 5l-8 8a2 2 0 0 1-2.8-2.8l7.4-7.4" />
    </Icon>
  );
}

export function ArrowUpIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 19V5M6 11l6-6 6 6" />
    </Icon>
  );
}

export function StopIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="7" y="7" width="10" height="10" rx="1.6" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function ToolIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17v3h3l5.3-5.3a4 4 0 0 0 5.4-5.4l-2.1 2.1-2.8-2.8Z" />
    </Icon>
  );
}

export function FolderIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3.5 7.5A2.5 2.5 0 0 1 6 5h3l2 2.5h7a2.5 2.5 0 0 1 2.5 2.5v7A2.5 2.5 0 0 1 18 19.5H6A2.5 2.5 0 0 1 3.5 17Z" />
    </Icon>
  );
}

export function CopyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="9" y="9" width="10.5" height="10.5" rx="2" />
      <path d="M15 9V6.5A2.5 2.5 0 0 0 12.5 4h-6A2.5 2.5 0 0 0 4 6.5v6A2.5 2.5 0 0 0 6.5 15H9" />
    </Icon>
  );
}

export function DownloadIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 4v10M8 10.5l4 4 4-4M5 19h14" />
    </Icon>
  );
}

export function EyeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M2.8 12s3.4-5.3 9.2-5.3S21.2 12 21.2 12s-3.4 5.3-9.2 5.3S2.8 12 2.8 12Z" />
      <circle cx="12" cy="12" r="2.6" />
    </Icon>
  );
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m6 9.5 6 6 6-6" />
    </Icon>
  );
}

export function ChevronRightIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m9.5 6 6 6-6 6" />
    </Icon>
  );
}

export function SparkIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3.5 13.7 9l5.5 1.7-5.5 1.7L12 18l-1.7-5.6L4.8 10.7 10.3 9Z" />
      <path d="M19 16.5v3M17.5 18h3" />
    </Icon>
  );
}

export function AlertIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 4 21 20H3Z" />
      <path d="M12 9.5v4.2M12 17.2v.1" />
    </Icon>
  );
}

export function InfoIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 11v5M12 8v.1" />
    </Icon>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 6l12 12M18 6 6 18" />
    </Icon>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m5 12.5 4.4 4.4L19 6.8" />
    </Icon>
  );
}

export function FileIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 3.8h7.2L18.5 9v11.2H6Z" />
      <path d="M13 3.8V9h5.5M9 13h6M9 16.5h4.5" />
    </Icon>
  );
}

export function RefreshIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M20 11a8 8 0 1 0-2.3 5.7" />
      <path d="M20 4.5V11h-6.5" />
    </Icon>
  );
}

/** 品牌标识：国网绿圆角方块 + 抽象电网/火花。 */
export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <span className="brand-mark" style={{ width: size, height: size }} aria-hidden="true">
      <svg
        width={size * 0.62}
        height={size * 0.62}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M12 3.5 13.8 9l5.2 1.6-5.2 1.6L12 17.8l-1.8-5.6L5 10.6 10.2 9Z" />
        <path d="M18.5 16.5v3M17 18h3" />
      </svg>
    </span>
  );
}
