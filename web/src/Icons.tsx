import type { CSSProperties } from "react";

const paths: Record<string, React.ReactNode> = {
  grid: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>
  ),
  camera: (
    <>
      <path d="M8 5l1.5-2h5L16 5h4a2 2 0 012 2v12a2 2 0 01-2 2H4a2 2 0 01-2-2V7a2 2 0 012-2z" />
      <circle cx="12" cy="13" r="4" />
    </>
  ),
  video: (
    <>
      <rect x="3" y="5" width="13" height="14" rx="3" />
      <path d="M16 10l5-3v10l-5-3" />
    </>
  ),
  terminal: (
    <>
      <rect x="2" y="3" width="20" height="18" rx="3" />
      <path d="M6 8l4 4-4 4m7 0h5" />
    </>
  ),
  activity: <path d="M2 12h5l3-8 4 16 3-8h5" />,
  book: (
    <>
      <path d="M12 5v16M12 5C8 2 4 3 2 4v15c3-1 7-1 10 2 3-3 7-3 10-2V4c-2-1-6-2-10 1z" />
    </>
  ),
  arrow: <path d="M7 17L17 7M7 7h10v10" />,
  chevron: <path d="M9 5l7 7-7 7" />,
  refresh: (
    <>
      <path d="M20 7v5h-5M4 17v-5h5" />
      <path d="M6 6a8 8 0 0113 3M5 15a8 8 0 0013 3" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 018 0v3M12 14v3" />
    </>
  ),
  unlock: (
    <>
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 017-2M12 14v3" />
    </>
  ),
  battery: (
    <>
      <rect x="2" y="6" width="18" height="12" rx="3" />
      <path d="M22 10v4M6 10v4m4-4v4m4-4v4" />
    </>
  ),
  wifi: (
    <>
      <path d="M2 8a16 16 0 0120 0M5 12a11 11 0 0114 0M8.5 16a5 5 0 017 0" />
      <circle cx="12" cy="20" r=".5" />
    </>
  ),
  robot: (
    <>
      <rect x="4" y="7" width="16" height="14" rx="4" />
      <path d="M12 3v4M1 11v6m22-6v6M9 17h6" />
      <circle cx="8.5" cy="12" r=".8" />
      <circle cx="15.5" cy="12" r=".8" />
      <circle cx="12" cy="2" r="1" />
    </>
  ),
  volume: (
    <>
      <path d="M3 9h4l5-4v14l-5-4H3zM16 8a6 6 0 010 8m3-11a10 10 0 010 14" />
    </>
  ),
  mic: (
    <>
      <rect x="9" y="2" width="6" height="13" rx="3" />
      <path d="M5 10v2a7 7 0 0014 0v-2M12 19v3m-4 0h8" />
    </>
  ),
  play: <path d="M8 4l12 8-12 8z" />,
  pause: (
    <>
      <path d="M8 5v14M16 5v14" />
    </>
  ),
  expand: <path d="M3 9V3h6m6 0h6v6m0 6v6h-6m-6 0H3v-6" />,
  download: (
    <>
      <path d="M12 3v12m-5-5l5 5 5-5M4 16v5h16v-5" />
    </>
  ),
  close: <path d="M6 6l12 12M6 18L18 6" />,
  check: <path d="M5 12l4 4L19 6" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 10v6m0-10v.5" />
    </>
  ),
  code: (
    <>
      <path d="M7 7l-5 5 5 5m10-10l5 5-5 5m-4-14l-2 18" />
    </>
  ),
  move: (
    <>
      <path d="M12 2v20M2 12h20M8 6l4-4 4 4m-8 12l4 4 4-4M6 8l-4 4 4 4m12-8l4 4-4 4" />
    </>
  ),
  up: <path d="M12 20V4M5 11l7-7 7 7" />,
  left: <path d="M20 12H4m7-7l-7 7 7 7" />,
  right: <path d="M4 12h16m-7-7l7 7-7 7" />,
  down: <path d="M12 4v16m-7-7l7 7 7-7" />,
  stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
  wave: (
    <>
      <path d="M3 9v6m4-10v14m5-17v20m5-17v14m4-10v6" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
};

export function Icon({
  name,
  size = 20,
  className = "",
  style,
}: {
  name: string;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
    >
      {paths[name] ?? paths.code}
    </svg>
  );
}
