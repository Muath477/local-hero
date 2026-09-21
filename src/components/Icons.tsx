import type { CSSProperties } from "react";

type IconProps = { size?: number; className?: string; style?: CSSProperties };

const base = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function PencilIcon({ size = 14, className, style }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} style={style} {...base}>
      <path d="M4 20.5h4.2L19.8 8.9a2.4 2.4 0 0 0 0-3.4l-1.3-1.3a2.4 2.4 0 0 0-3.4 0L3.7 15.8Z" />
      <path d="m14.3 5.4 4.3 4.3" />
    </svg>
  );
}

export function TrashIcon({ size = 14, className, style }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} style={style} {...base}>
      <path d="M5 7.5h14M9.5 7.5V5.2a1.2 1.2 0 0 1 1.2-1.2h2.6a1.2 1.2 0 0 1 1.2 1.2v2.3" />
      <path d="M7 7.5 7.8 19a1.6 1.6 0 0 0 1.6 1.5h5.2a1.6 1.6 0 0 0 1.6-1.5l.8-11.5" />
      <path d="M10.3 11v6M13.7 11v6" />
    </svg>
  );
}

export function ChartIcon({ size = 15, className, style }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} style={style} {...base}>
      <path d="M4.5 20.5h15" />
      <path d="M7.5 20.5v-6M12 20.5V8.5M16.5 20.5v-9.8" />
    </svg>
  );
}

export function GearIcon({ size = 15, className, style }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} style={style} {...base}>
      <circle cx="12" cy="12" r="2.9" />
      <path d="M12 3.5v2.3M12 18.2v2.3M20.5 12h-2.3M5.8 12H3.5M17.7 6.3l-1.6 1.6M7.9 16.1l-1.6 1.6M17.7 17.7l-1.6-1.6M7.9 7.9 6.3 6.3" />
    </svg>
  );
}

export function SunIcon({ size = 16, className, style }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} style={style} {...base}>
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.8v2.5M12 18.7v2.5M21.2 12h-2.5M5.3 12H2.8M18.5 5.5l-1.8 1.8M7.3 16.7l-1.8 1.8M18.5 18.5l-1.8-1.8M7.3 7.3 5.5 5.5" />
    </svg>
  );
}

export function MoonIcon({ size = 16, className, style }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} style={style} {...base}>
      <path d="M20 14.2A8.3 8.3 0 1 1 9.8 4a6.6 6.6 0 0 0 10.2 10.2Z" />
    </svg>
  );
}

export function FileTextIcon({ size = 14, className, style }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} style={style} {...base}>
      <path d="M7 3.5h7L18.5 8v12.5h-11.5Z" />
      <path d="M14 3.5V8h4.5M9.2 12.5h5.6M9.2 15.7h5.6M9.2 18.9h3.5" />
    </svg>
  );
}

export function RefreshIcon({ size = 14, className, style }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} style={style} {...base}>
      <path d="M4.5 12a7.5 7.5 0 0 1 12.9-5.2M19.5 12a7.5 7.5 0 0 1-12.9 5.2" />
      <path d="M17.7 3.5v3.6h-3.6M6.3 20.5v-3.6h3.6" />
    </svg>
  );
}
